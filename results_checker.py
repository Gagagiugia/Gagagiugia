#!/usr/bin/env python3
"""
Recupera i risultati delle partite tramite API‑Football, aggiorna bets.json e bets_log.csv.
Invia report Telegram.
"""

import os, json, csv, logging, requests
from datetime import date, timedelta

API_FOOTBALL_KEY = os.environ["API_FOOTBALL_KEY"]
TELEGRAM_TOKEN = os.environ["TELEGRAM_TOKEN"]
TELEGRAM_CHAT_ID = os.environ["TELEGRAM_CHAT_ID"]

def send_telegram(text):
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    try:
        resp = requests.post(url, json={"chat_id": TELEGRAM_CHAT_ID, "text": text}, timeout=10)
        if resp.status_code != 200:
            logging.error(f"TELEGRAM ERROR {resp.status_code}: {resp.text[:200]}")
    except Exception as e:
        logging.error(f"TELEGRAM EXCEPTION: {e}")

def load_json(filename, default=None):
    try:
        with open(filename) as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return default if default is not None else {}

def save_json(filename, data):
    with open(filename, "w") as f:
        json.dump(data, f, indent=2)

def normalize(name):
    return name.lower().strip() if name else ""

def get_results_last_days(days=3):
    """Recupera i risultati degli ultimi `days` giorni da API-Football.
    Chiave: (home_norm, away_norm) -> {winner, score}.
    """
    headers = {"x-apisports-key": API_FOOTBALL_KEY, "x-apisports-host": "v3.football.api-sports.io"}
    url = "https://v3.football.api-sports.io/fixtures"
    results = {}
    for delta in range(0, days + 1):
        target = (date.today() - timedelta(days=delta)).strftime("%Y-%m-%d")
        try:
            resp = requests.get(url, headers=headers, params={"date": target}, timeout=15)
            if resp.status_code != 200:
                logging.error(f"API fixtures {target} HTTP {resp.status_code}")
                continue
            data = resp.json()
            for match in data.get("response", []):
                goals = match["goals"]
                if goals["home"] is None or goals["away"] is None:
                    continue
                home_team = match["teams"]["home"]["name"]
                away_team = match["teams"]["away"]["name"]
                hs = goals["home"]
                aws = goals["away"]
                if hs > aws:
                    winner = home_team
                elif aws > hs:
                    winner = away_team
                else:
                    winner = "draw"
                key = (normalize(home_team), normalize(away_team))
                results[key] = {"winner": winner, "score": f"{hs}-{aws}"}
        except Exception as e:
            logging.error(f"Errore fetch {target}: {e}")
    return results

def update_csv_with_bets(bets, filename="bets_log.csv"):
    """Aggiorna il CSV usando i dati di bets (con risultati già risolti)."""
    if not os.path.isfile(filename):
        logging.info("bets_log.csv non trovato.")
        return
    with open(filename, "r", newline="") as f:
        rows = list(csv.reader(f))
    if not rows:
        return
    header = rows[0]
    try:
        idx_id = header.index("fixture_id")
        idx_score = header.index("Risultato reale")
        idx_esito = header.index("Esito")
    except ValueError:
        logging.error("Colonne mancanti nel CSV.")
        return

    bets_by_id = {str(b.get("fixture_id", "")): b for b in bets}

    updated = 0
    for i, row in enumerate(rows[1:], start=1):
        if row[idx_esito] != "":
            continue
        b = bets_by_id.get(row[idx_id])
        if not b:
            continue
        if b.get("result") in ("won", "lost"):
            rows[i][idx_score] = b.get("score", "")
            rows[i][idx_esito] = b.get("result", "")
            updated += 1

    with open(filename, "w", newline="") as f:
        writer = csv.writer(f)
        writer.writerows(rows)
    logging.info(f"CSV aggiornato: {updated} righe.")

def main():
    bets = load_json("bets.json", [])
    if not bets:
        logging.info("Nessun bet nel file.")
        return

    results = get_results_last_days(days=3)

    updated = 0
    for b in bets:
        if b.get("result") != "pending":
            continue
        key = (normalize(b.get("home_team", "")), normalize(b.get("away_team", "")))
        if key not in results:
            continue
        info = results[key]
        b["score"] = info["score"]
        if info["winner"] == "draw":
            b["result"] = "lost"
        else:
            if normalize(b.get("predicted_winner", "")) == normalize(info["winner"]):
                b["result"] = "won"
            else:
                b["result"] = "lost"
        updated += 1

    if updated > 0:
        save_json("bets.json", bets)
        update_csv_with_bets(bets)
        won = sum(1 for b in bets if b.get("result") == "won")
        lost = sum(1 for b in bets if b.get("result") == "lost")
        total = won + lost
        acc = (won / total * 100) if total > 0 else 0
        report = f"📊 Report risultati\nPronostici verificati: {updated}\n✅ Vinti: {won}\n❌ Persi: {lost}\n📈 Accuratezza: {acc:.1f}%"
        send_telegram(report)
        logging.info(report)
    else:
        logging.info("Nessun risultato disponibile per i bet pending.")

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
    main()
