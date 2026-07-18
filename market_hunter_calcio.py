#!/usr/bin/env python3
"""
Market Hunter Calcio – The Odds API Edition
Ogni 15 minuti (solo weekend) controlla le quote di campionati minori e invia alert Telegram.
"""

import os
import json
import logging
import requests
from datetime import datetime, date, timedelta

API_KEY = os.environ["API_KEY"]
TELEGRAM_TOKEN = os.environ["TELEGRAM_TOKEN"]
TELEGRAM_CHAT_ID = os.environ["TELEGRAM_CHAT_ID"]

# Impostazioni
CRASH_THRESHOLD_PERCENT = 25
MAX_HOURS_CRASH_WINDOW = 0.5        # 30 minuti
MIN_STARTING_ODD = 1.50
MAX_CRASH_ODD = 1.50

# Campionati da monitorare (The Odds API)
TARGET_SPORT_KEYS = [
    "soccer_italy_serie_c",
    "soccer_italy_serie_d",
    "soccer_england_national_league",
    "soccer_spain_segunda_b",
    "soccer_germany_regionalliga",
    "soccer_france_national",
    "soccer_brazil_campeonato_serie_c",
    "soccer_brazil_campeonato_serie_d",
    "soccer_argentina_primera_nacional",
    "soccer_argentina_primera_b",
    "soccer_argentina_primera_c",
    "soccer_sweden_allsvenskan",
    "soccer_sweden_superettan",
    "soccer_norway_eliteserien",
    "soccer_finland_veikkausliiga",
    "soccer_estonia_meistriliiga",
    "soccer_latvia_virsliga",
]

def send_telegram(text):
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    try:
        requests.post(url, json={"chat_id": TELEGRAM_CHAT_ID, "text": text, "parse_mode": "Markdown"}, timeout=10)
    except Exception as e:
        logging.error(f"Telegram error: {e}")

def load_json(filename, default=None):
    try:
        with open(filename) as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return default if default is not None else {}

def save_json(filename, data):
    with open(filename, "w") as f:
        json.dump(data, f, indent=2)

def fetch_odds():
    url = "https://api.the-odds-api.com/v4/sports/soccer/odds/"
    params = {
        "apiKey": API_KEY,
        "regions": "eu",
        "markets": "h2h",
        "dateFormat": "iso",
        "oddsFormat": "decimal",
        "includeLinks": "false",
        "includeSids": "false"
    }
    try:
        resp = requests.get(url, params=params, timeout=15)
        if resp.status_code != 200:
            logging.error(f"HTTP {resp.status_code}: {resp.text}")
            return []
        data = resp.json()
        matches = []
        for game in data:
            sport_key = game.get("sport_key")
            if sport_key not in TARGET_SPORT_KEYS:
                continue
            home = game["home_team"]
            away = game["away_team"]
            bookmakers = game.get("bookmakers", [])
            if not bookmakers:
                continue
            bk = None
            for b in bookmakers:
                if b["key"] == "bet365":
                    bk = b
                    break
            if not bk:
                bk = bookmakers[0]

            markets = bk.get("markets", [])
            if not markets:
                continue
            h2h = markets[0]
            outcomes = h2h.get("outcomes", [])
            odd_home = odd_away = odd_draw = None
            for o in outcomes:
                if o["name"] == home:
                    odd_home = o["price"]
                elif o["name"] == away:
                    odd_away = o["price"]
                elif o["name"] == "Draw":
                    odd_draw = o["price"]
            if odd_home and odd_away:
                matches.append({
                    "fixture_id": game["id"],
                    "home": home,
                    "away": away,
                    "league": game["sport_title"] + " - " + game.get("sport_key", ""),
                    "odd_home": odd_home,
                    "odd_away": odd_away,
                    "odd_draw": odd_draw
                })
        return matches
    except Exception as e:
        logging.error(f"API call failed: {e}")
        return []

def check_crashes(state, current_matches, now):
    alerts = []
    new_state = {}
    threshold_time = now - timedelta(hours=MAX_HOURS_CRASH_WINDOW)

    for m in current_matches:
        fid = m["fixture_id"]
        new_state[fid] = {
            "home": m["home"],
            "away": m["away"],
            "league": m["league"],
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

        if (now - prev_time) > timedelta(hours=MAX_HOURS_CRASH_WINDOW):
            continue

        old_home = prev["odd_home"]
        old_away = prev["odd_away"]

        if old_home > MIN_STARTING_ODD and m["odd_home"] < MAX_CRASH_ODD:
            drop = (old_home - m["odd_home"]) / old_home
            if drop >= CRASH_THRESHOLD_PERCENT / 100.0:
                alerts.append({
                    "fixture_id": fid,
                    "home": m["home"],
                    "away": m["away"],
                    "league": m["league"],
                    "side": "Home",
                    "old_odd": old_home,
                    "new_odd": m["odd_home"],
                    "drop": round(drop * 100, 2),
                    "predicted": m["home"],
                    "time": now.strftime("%H:%M:%S")
                })

        if old_away > MIN_STARTING_ODD and m["odd_away"] < MAX_CRASH_ODD:
            drop = (old_away - m["odd_away"]) / old_away
            if drop >= CRASH_THRESHOLD_PERCENT / 100.0:
                alerts.append({
                    "fixture_id": fid,
                    "home": m["home"],
                    "away": m["away"],
                    "league": m["league"],
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

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
    logging.info("Market Hunter Calcio (Odds API) started")

    state = load_json("state.json")
    bets = load_json("bets.json", [])

    matches = fetch_odds()
    logging.info(f"Trovate {len(matches)} partite nelle leghe target")

    now = datetime.now()
    alerts, new_state = check_crashes(state, matches, now)

    for alert in alerts:
        message = (
            f"🚨 *CRASH RILEVATO*\n"
            f"⚽ {alert['league']}\n"
            f"⚔️ {alert['home']} vs {alert['away']}\n"
            f"📉 Quota {alert['predicted']}: {alert['old_odd']:.2f} → {alert['new_odd']:.2f} (-{alert['drop']}%)\n"
            f"⏱️ Rilevato alle {alert['time']}\n"
            f"🔮 Pronostico: *{alert['predicted']}* vincitore"
        )
        send_telegram(message)
        bets = save_bet(bets, alert)

    save_json("state.json", new_state)
    save_json("bets.json", bets)

    logging.info(f"Inviate {len(alerts)} notifiche. Stato salvato.")
