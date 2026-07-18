#!/usr/bin/env python3
"""
Market Hunter Basket – GitHub Actions Edition
Rileva crolli di quota e invia alert Telegram. Salva lo stato nella cache di GitHub.
"""

import os
import json
import logging
import requests
from datetime import datetime, date, timedelta

# ------------------------- CONFIGURAZIONE -------------------------
API_KEY = os.environ["API_KEY"]
TELEGRAM_TOKEN = os.environ["TELEGRAM_TOKEN"]
TELEGRAM_CHAT_ID = os.environ["TELEGRAM_CHAT_ID"]

CRASH_THRESHOLD_PERCENT = 25      # calo percentuale minimo
MAX_MINUTES_CRASH_WINDOW = 30     # finestra temporale per il crollo (minuti)
MIN_STARTING_ODD = 1.50           # quota di partenza minima per considerare il crollo
MAX_CRASH_ODD = 1.50              # quota finale massima dopo il crollo

BOOKMAKER_ID = 1                  # 1 = 1xBet (prova anche 8 per Bet365 se preferisci)
BET_ID = 1                        # Winner (moneyline)

# Leghe minori di basket, attive in estate
TARGET_LEAGUES = [
    120,  # Serie A2 Basket (Italia)
    121,  # Serie B Basket (Italia)
    212,  # LEB Oro (Spagna)
    213,  # LEB Plata (Spagna)
    129,  # ProA (Germania)
    130,  # ProB (Germania)
    185,  # LNB Pro B (Francia)
    56,   # NBB (Brasile) – se ancora in corso
    319,  # PBA Philippine Cup
    296,  # Superliga Profesional (Venezuela)
    55,   # Liga Nacional (Argentina)
    222,  # Liga Uruguaya
]

# ------------------------- FUNZIONI -------------------------
def send_telegram(text: str):
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    try:
        requests.post(url, json={
            "chat_id": TELEGRAM_CHAT_ID, "text": text, "parse_mode": "Markdown"
        }, timeout=10)
    except Exception as e:
        logging.error(f"Telegram error: {e}")

def load_json(filename: str, default=None):
    try:
        with open(filename, "r") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return default if default is not None else {}

def save_json(filename: str, data):
    with open(filename, "w") as f:
        json.dump(data, f, indent=2)

def fetch_odds():
    today = date.today().strftime("%Y-%m-%d")
    url = "https://v1.basketball.api-sports.io/odds"
    headers = {
        "x-apisports-key": API_KEY,
        "x-apisports-host": "v1.basketball.api-sports.io"
    }
    params = {"date": today, "bookmaker": BOOKMAKER_ID, "bet": BET_ID}
    try:
        resp = requests.get(url, headers=headers, params=params, timeout=15)
        if resp.status_code != 200:
            logging.error(f"API HTTP {resp.status_code}: {resp.text}")
            return []
        data = resp.json()
        if data.get("errors"):
            logging.error(f"API errors: {data['errors']}")
            return []
        matches = []
        for item in data.get("response", []):
            league = item.get("league", {})
            if league.get("id") not in TARGET_LEAGUES:
                continue
            fixture = item.get("fixture", {})
            fixture_id = fixture.get("id")
            if not fixture_id:
                continue
            home = item.get("teams", {}).get("home", {}).get("name", "?")
            away = item.get("teams", {}).get("away", {}).get("name", "?")
            try:
                odds_values = item["bookmakers"][0]["bets"][0]["values"]
                odd_home = next((float(o["odd"]) for o in odds_values if o["value"] == "Home"), None)
                odd_away = next((float(o["odd"]) for o in odds_values if o["value"] == "Away"), None)
            except (IndexError, KeyError, StopIteration):
                continue
            if odd_home and odd_away:
                matches.append({
                    "fixture_id": fixture_id,
                    "home": home,
                    "away": away,
                    "league_id": league["id"],
                    "league_name": league.get("name", "?"),
                    "odd_home": odd_home,
                    "odd_away": odd_away
                })
        return matches
    except Exception as e:
        logging.error(f"API call failed: {e}")
        return []

def check_crashes(state, current_matches, now):
    alerts = []
    new_state = {}
    threshold_time = now - timedelta(minutes=MAX_MINUTES_CRASH_WINDOW)

    for m in current_matches:
        fid = str(m["fixture_id"])
        # Salva comunque la nuova quota nello stato
        new_state[fid] = {
            "home": m["home"],
            "away": m["away"],
            "league": m["league_name"],
            "odd_home": m["odd_home"],
            "odd_away": m["odd_away"],
            "timestamp": now.isoformat()
        }

        if fid not in state:
            continue

        prev = state[fid]
        try:
            prev_time = datetime.fromisoformat(prev["timestamp"])
        except (ValueError, KeyError):
            continue

        # Controlla se la vecchia rilevazione è entro la finestra di crash
        if (now - prev_time) > timedelta(minutes=MAX_MINUTES_CRASH_WINDOW):
            continue

        old_home = prev["odd_home"]
        old_away = prev["odd_away"]

        # Crollo Home
        if old_home > MIN_STARTING_ODD and m["odd_home"] < MAX_CRASH_ODD:
            drop = (old_home - m["odd_home"]) / old_home
            if drop >= CRASH_THRESHOLD_PERCENT / 100.0:
                alerts.append({
                    "fixture_id": fid,
                    "home": m["home"],
                    "away": m["away"],
                    "league": m["league_name"],
                    "side": "Home",
                    "old_odd": old_home,
                    "new_odd": m["odd_home"],
                    "drop": round(drop * 100, 2),
                    "predicted": m["home"],
                    "time": now.strftime("%H:%M:%S")
                })
        # Crollo Away
        if old_away > MIN_STARTING_ODD and m["odd_away"] < MAX_CRASH_ODD:
            drop = (old_away - m["odd_away"]) / old_away
            if drop >= CRASH_THRESHOLD_PERCENT / 100.0:
                alerts.append({
                    "fixture_id": fid,
                    "home": m["home"],
                    "away": m["away"],
                    "league": m["league_name"],
                    "side": "Away",
                    "old_odd": old_away,
                    "new_odd": m["odd_away"],
                    "drop": round(drop * 100, 2),
                    "predicted": m["away"],
                    "time": now.strftime("%H:%M:%S")
                })

    return alerts, new_state

def save_bet(bets, alert):
    fid = alert["fixture_id"]
    # Evita di registrare lo stesso match più volte nello stesso giorno
    for b in bets:
        if b["fixture_id"] == fid and b["side"] == alert["side"]:
            return bets
    bets.append({
        "fixture_id": fid,
        "predicted_winner": alert["predicted"],
        "odd_at_crash": alert["new_odd"],
        "crash_percent": alert["drop"],
        "timestamp": datetime.now().isoformat(),
        "result": "pending"
    })
    return bets

# ------------------------- MAIN -------------------------
if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
    logging.info("Market Hunter Basket started")

    state = load_json("state.json")
    bets = load_json("bets.json", [])

    matches = fetch_odds()
    logging.info(f"Trovate {len(matches)} partite nelle leghe target")

    now = datetime.now()
    alerts, new_state = check_crashes(state, matches, now)

    for alert in alerts:
        message = (
            f"🚨 *CRASH RILEVATO*\n"
            f"🏀 {alert['league']}\n"
            f"⚔️ {alert['home']} vs {alert['away']}\n"
            f"📉 Quota {alert['predicted']}: {alert['old_odd']:.2f} → {alert['new_odd']:.2f} (-{alert['drop']}%)\n"
            f"⏱️ Rilevato alle {alert['time']}\n"
            f"🔮 Pronostico: *{alert['predicted']}* vincitore"
        )
        send_telegram(message)
        bets = save_bet(bets, alert)

    # Salva i nuovi stati
    save_json("state.json", new_state)
    save_json("bets.json", bets)

    logging.info(f"Inviate {len(alerts)} notifiche. Stato salvato.")
