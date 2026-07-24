#!/usr/bin/env python3
"""
Market Hunter Calcio – Final Edition
Lista leghe adattata alla disponibilità reale di oggi 24 luglio 2026.
"""

import os, json, logging, requests, sys
from datetime import datetime, date, timedelta

API_KEY = os.environ["API_KEY"]
TELEGRAM_TOKEN = os.environ["TELEGRAM_TOKEN"]
TELEGRAM_CHAT_ID = os.environ["TELEGRAM_CHAT_ID"]

# Soglie
CRASH_THRESHOLD_PERCENT = 25
MAX_MINUTES_CRASH_WINDOW = 30
MIN_STARTING_ODD = 1.50
MAX_CRASH_ODD = 1.50
HOURS_BEFORE_KICKOFF = 2

# Campionati disponibili oggi (dal debug)
# Nota: in futuro potrai ripristinare la lista completa commentata in fondo.
TARGET_SPORT_KEYS = [
    "soccer_argentina_primera_division",
    "soccer_denmark_superliga",
    "soccer_finland_veikkausliiga",
    "soccer_league_of_ireland",
    "soccer_poland_ekstraklasa",
    "soccer_russia_premier_league",
    "soccer_sweden_allsvenskan",
]

# Lista originale (per i weekend futuri)
# TARGET_SPORT_KEYS = [
#     "soccer_italy_serie_c",
#     "soccer_italy_serie_d",
#     "soccer_england_national_league",
#     "soccer_spain_segunda_b",
#     "soccer_germany_regionalliga",
#     "soccer_france_national",
#     "soccer_brazil_campeonato_serie_c",
#     "soccer_brazil_campeonato_serie_d",
#     "soccer_argentina_primera_nacional",
#     "soccer_argentina_primera_b",
#     "soccer_argentina_primera_c",
#     "soccer_sweden_allsvenskan",
#     "soccer_sweden_superettan",
#     "soccer_norway_eliteserien",
#     "soccer_finland_veikkausliiga",
#     "soccer_estonia_meistriliiga",
#     "soccer_latvia_virsliga",
# ]

def is_monitoring_window():
    now = datetime.utcnow()
    if now.weekday() not in (4, 5, 6):
        return False
    if not (11 <= now.hour <= 21):
        return False
    return True

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

def extract_odds(bk, home, away):
    for market in bk.get("markets", []):
        if market["key"] == "h2h":
            outcomes = market["outcomes"]
            odd_home = next((o["price"] for o in outcomes if o["name"] == home), None)
            odd_away = next((o["price"] for o in outcomes if o["name"] == away), None)
            return odd_home, odd_away
    return None, None

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
            commence_time = game.get("commence_time")

            bookmakers = game.get("bookmakers", [])
            bk_bet365 = None
            bk_other = None
            for b in bookmakers:
                key = b["key"]
                if key == "bet365":
                    bk_bet365 = b
                elif key in ("unibet", "williamhill", "marathonbet"):
                    if not bk_other:
                        bk_other = b
            if not bk_bet365 or not bk_other:
                continue

            odd_home_b365, odd_away_b365 = extract_odds(bk_bet365, home, away)
            odd_home_other, odd_away_other = extract_odds(bk_other, home, away)
            if not all([odd_home_b365, odd_away_b365, odd_home_other, odd_away_other]):
                continue

            matches.append({
                "fixture_id": game["id"],
                "home": home,
                "away": away,
                "league": game["sport_title"] + " - " + sport_key,
                "commence_time": commence_time,
                "odd_home_b365": odd_home_b365,
                "odd_away_b365": odd_away_b365,
                "odd_home_other": odd_home_other,
                "odd_away_other": odd_away_other,
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
        if m.get("commence_time"):
            try:
                kickoff = datetime.fromisoformat(m["commence_time"].replace("Z", "+00:00"))
                if (kickoff - now).total_seconds() > HOURS_BEFORE_KICKOFF * 3600:
                    continue
            except:
                pass

        fid = m["fixture_id"]
        new_state[fid] = {
            "home": m["home"],
            "away": m["away"],
            "league": m["league"],
            "odd_home_b365": m["odd_home_b365"],
            "odd_away_b365": m["odd_away_b365"],
            "odd_home_other": m["odd_home_other"],
            "odd_away_other": m["odd_away_other"],
            "timestamp": now.isoformat()
        }

        if fid not in state:
            continue

        prev = state[fid]
        try:
            prev_time = datetime.fromisoformat(prev["timestamp"])
        except:
            continue
        if (now - prev_time) > timedelta(minutes=MAX_MINUTES_CRASH_WINDOW):
            continue

        drop_home_b365 = (prev["odd_home_b365"] - m["odd_home_b365"]) / prev["odd_home_b365"]
        drop_away_b365 = (prev["odd_away_b365"] - m["odd_away_b365"]) / prev["odd_away_b365"]
        drop_home_other = (prev["odd_home_other"] - m["odd_home_other"]) / prev["odd_home_other"]
        drop_away_other = (prev["odd_away_other"] - m["odd_away_other"]) / prev["odd_away_other"]

        for side, drop_b365, drop_other, old_b365, new_b365 in [
            ("Home", drop_home_b365, drop_home_other, prev["odd_home_b365"], m["odd_home_b365"]),
            ("Away", drop_away_b365, drop_away_other, prev["odd_away_b365"], m["odd_away_b365"])
        ]:
            if (old_b365 > MIN_STARTING_ODD and new_b365 < MAX_CRASH_ODD and
                drop_b365 >= CRASH_THRESHOLD_PERCENT / 100.0 and
                drop_other >= CRASH_THRESHOLD_PERCENT / 100.0):
                alerts.append({
                    "fixture_id": fid,
                    "home": m["home"],
                    "away": m["away"],
                    "league": m["league"],
                    "side": side,
                    "old_odd": old_b365,
                    "new_odd": new_b365,
                    "drop": round(drop_b365 * 100, 2),
                    "predicted": m["home"] if side == "Home" else m["away"],
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
        "home_team": alert["home"],
        "away_team": alert["away"],
        "predicted_winner": alert["predicted"],
        "odd_at_crash": alert["new_odd"],
        "crash_percent": alert["drop"],
        "timestamp": datetime.now().isoformat(),
        "result": "pending"
    })
    return bets

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")

    if not is_monitoring_window():
        logging.info("Fuori dalla finestra di monitoraggio. Esco.")
        sys.exit(0)

    logging.info("Market Hunter Calcio (Final) started")

    state = load_json("state.json")
    bets = load_json("bets.json", [])

    matches = fetch_odds()
    logging.info(f"Trovate {len(matches)} partite nei campionati target")

    now = datetime.now()
    alerts, new_state = check_crashes(state, matches, now)

    for alert in alerts:
        message = (
            f"🚨 *CRASH CALCIO*\n"
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

    solved = [b for b in bets if b["result"] != "pending"]
    if solved:
        won = sum(1 for b in solved if b["result"] == "won")
        logging.info(f"RIEPILOGO: {won}/{len(solved)} vinti ({100*won/len(solved):.1f}%)")
    else:
        logging.info("Nessun bet risolto ancora.")

    logging.info(f"Inviate {len(alerts)} notifiche. Stato salvato.")
