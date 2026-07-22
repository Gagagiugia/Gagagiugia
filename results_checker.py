#!/usr/bin/env python3
"""
Recupera i risultati delle partite tramite API‑Football e aggiorna bets.json.
Invia report Telegram.
"""

import os, json, logging, requests
from datetime import date

API_FOOTBALL_KEY = os.environ["API_FOOTBALL_KEY"]
TELEGRAM_TOKEN = os.environ["TELEGRAM_TOKEN"]
TELEGRAM_CHAT_ID = os.environ["TELEGRAM_CHAT_ID"]

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

def get_results():
    today = date.today().strftime("%Y-%m-%d")
    headers = {"x-apisports-key": API_FOOTBALL_KEY, "x-apisports-host": "v3.football.api-sports.io"}
    url = "https://v3.football.api-sports.io/fixtures"
    params = {"date": today}
    try:
        resp = requests.get(url, headers=headers, params=params, timeout=15)
        if resp.status_code != 200:
            logging.error(f"API fixtures HTTP {resp.status_code}")
            return {}
        data = resp.json()
        results = {}
        for match in data.get("response", []):
            fid = match["fixture"]["id"]
            goals = match["goals"]
            if goals["home"] is not None and goals["away"] is not None:
                if goals["home"] > goals["away"]:
                    winner = match["teams"]["home"]["name"]
                elif goals["away"] > goals["home"]:
                    winner = match["teams"]["away"]["name"]
                else:
                    winner = "draw"
                results[fid] = winner
        return results
    except Exception as e:
        logging.error(f"Error fetching results: {e}")
        return {}

def main():
    bets = load_json("bets.json", [])
    pending = [b for b in bets if b["result"] == "pending"]
    if not pending:
        logging.info("Nessuna scommessa pending.")
        return

    results = get_results()
    updated = 0
    for b in pending:
        fid = b["fixture_id"]
        if fid in results:
            actual = results[fid]
            if actual == "draw":
                b["result"] = "lost"
            else:
                b["result"] = "won" if b["predicted_winner"] == actual else "lost"
            updated += 1

    if updated > 0:
        save_json("bets.json", bets)
        won = sum(1 for b in bets if b["result"] == "won")
        lost = sum(1 for b in bets if b["result"] == "lost")
        total = won + lost
        acc = (won / total * 100) if total > 0 else 0
        report = f"📊 *Report risultati*\nPronostici verificati oggi: {updated}\n✅ Vinti: {won}\n❌ Persi: {lost}\n📈 Accuratezza: {acc:.1f}%"
        send_telegram(report)
        logging.info(report)
    else:
        logging.info("Nessun risultato disponibile per i bet pending.")

if __name__ == "__main__":
    main()
