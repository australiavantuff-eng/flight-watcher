import os
import json
import threading
import time
from datetime import datetime, timedelta
import requests
from flask import Flask, request

# =========================
# ENV VARIABLES
# =========================
TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN")
AMADEUS_API_KEY = os.environ.get("AMADEUS_API_KEY")
AMADEUS_API_SECRET = os.environ.get("AMADEUS_API_SECRET")
PORT = int(os.environ.get("PORT", 10000))

# =========================
# FLASK APP
# =========================
app = Flask(__name__)

# =========================
# STORAGE
# =========================
ROUTES_FILE = "routes.json"
TRENDS_CACHE_FILE = "trends_cache.json"

if os.path.exists(ROUTES_FILE):
    with open(ROUTES_FILE, "r") as f:
        ROUTES = json.load(f)
else:
    ROUTES = []

if os.path.exists(TRENDS_CACHE_FILE):
    with open(TRENDS_CACHE_FILE, "r") as f:
        TRENDS_CACHE = json.load(f)
else:
    TRENDS_CACHE = {}

lock = threading.Lock()

# =========================
# TELEGRAM QUEUE
# =========================
import queue

telegram_queue = queue.Queue()

def telegram_worker():
    while True:
        chat_id, text = telegram_queue.get()
        try:
            requests.post(
                f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage",
                json={"chat_id": chat_id, "text": text},
                timeout=10
            )
        except Exception as e:
            print("Telegram send error:", e)
        time.sleep(0.5)  # 0.5s delay to avoid spam
        telegram_queue.task_done()

# Start Telegram worker thread once
threading.Thread(target=telegram_worker, daemon=True).start()

def queue_telegram_message(chat_id, text):
    telegram_queue.put((chat_id, text))

# =========================
# FLASK ROUTES
# =========================
@app.route("/")
def home():
    return "✈️ Flight Watcher is running", 200

@app.route("/webhook", methods=["POST"])
def telegram_webhook():
    data = request.get_json(force=True)
    if "message" not in data:
        return "ok", 200

    message = data["message"]
    chat_id = message["chat"]["id"]
    text = message.get("text", "")

    # --- START COMMAND ---
    if text == "/start":
        queue_telegram_message(
            chat_id,
            "Welcome to Flight Watcher ✈️\n\n"
            "Send your route in this format:\n"
            "`KTM BKK 7 10 200`\n"
            "Meaning: Origin Destination MinDays MaxDays MaxPrice\n\n"
            "To see trends, use:\n"
            "`/trends KTM BKK 2026-03-01 2026-03-31`"
        )
        return "ok", 200

    # --- TRENDS COMMAND ---
    if text.startswith("/trends"):
        parts = text.split()
        if len(parts) != 4:
            queue_telegram_message(chat_id, "❌ Format: /trends ORG DST YYYY-MM-DD YYYY-MM-DD")
            return "ok", 200

        origin, dest, start_date, end_date = parts[1:]
        cache_key = f"{origin}_{dest}_{start_date}_{end_date}"

        with lock:
            cached = TRENDS_CACHE.get(cache_key)
            if cached and (datetime.utcnow() - datetime.fromisoformat(cached["timestamp"])).total_seconds() < 86400:
                trends_summary = cached["summary"]
            else:
                # 🔮 Replace with real Amadeus API call in production
                trends_summary = f"Trends for {origin}→{dest}:\nAverage: $350\nMin: $300\nMax: $400"
                TRENDS_CACHE[cache_key] = {"summary": trends_summary, "timestamp": datetime.utcnow().isoformat()}
                with open(TRENDS_CACHE_FILE, "w") as f:
                    json.dump(TRENDS_CACHE, f, indent=2)

        queue_telegram_message(chat_id, trends_summary)
        return "ok", 200

    # --- ADD ROUTE COMMAND ---
    parts = text.split()
    if len(parts) != 5:
        queue_telegram_message(chat_id, "❌ Invalid format. Try:\nKTM BKK 7 10 200")
        return "ok", 200

    origin, dest, min_days, max_days, max_price = parts
    route = {
        "chat_id": chat_id,
        "origin": origin.upper(),
        "destination": dest.upper(),
        "min_days": int(min_days),
        "max_days": int(max_days),
        "max_price": float(max_price),
        "created_at": datetime.utcnow().isoformat(),
        "last_checked": None,
    }

    with lock:
        ROUTES.append(route)
        with open(ROUTES_FILE, "w") as f:
            json.dump(ROUTES, f, indent=2)

    queue_telegram_message(chat_id, "✅ Route added. Watching for deals!")
    return "ok", 200

# =========================
# AMADEUS TOKEN (stub)
# =========================
def get_amadeus_token():
    return "DUMMY_TOKEN"

# =========================
# ADAPTIVE WATCHER
# =========================
MIN_CHECK_INTERVAL = 3600  # seconds = 1 hour between checks

def adaptive_watcher():
    print("✈️ Adaptive watcher running")
    while True:
        try:
            time.sleep(1800)  # baseline sleep

            with lock:
                routes_snapshot = ROUTES.copy()

            for route in routes_snapshot:
                try:
                    last_checked = route.get("last_checked")
                    if last_checked:
                        last_checked_dt = datetime.fromisoformat(last_checked)
                        if (datetime.utcnow() - last_checked_dt).total_seconds() < MIN_CHECK_INTERVAL:
                            continue  # skip to save API calls

                    # 🔮 Amadeus API call placeholder
                    # price = fetch_price(route)
                    # if price <= route["max_price"]:
                    #     queue_telegram_message(route["chat_id"], f"🔥 DEAL FOUND: ${price}")

                    route["last_checked"] = datetime.utcnow().isoformat()

                except Exception as e:
                    print("Watcher error (route):", e)

            with lock:
                with open(ROUTES_FILE, "w") as f:
                    json.dump(ROUTES, f, indent=2)

        except Exception as e:
            print("Watcher error (global):", e)

# =========================
# STARTUP
# =========================
if __name__ == "__main__":
    print("🚀 Starting Flight Watcher service")

    # Only start watcher in main process (avoid duplicates under Gunicorn)
    if os.environ.get("GUNICORN_WORKER_ID") is None:
        watcher_thread = threading.Thread(target=adaptive_watcher, daemon=True)
        watcher_thread.start()

    app.run(host="0.0.0.0", port=PORT)
