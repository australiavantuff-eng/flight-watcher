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

if os.path.exists(ROUTES_FILE):
    with open(ROUTES_FILE, "r") as f:
        ROUTES = json.load(f)
else:
    ROUTES = []

lock = threading.Lock()

# =========================
# TELEGRAM HELPERS
# =========================
def send_message(chat_id, text):
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    payload = {"chat_id": chat_id, "text": text}
    try:
        requests.post(url, json=payload, timeout=10)
    except Exception as e:
        print("Telegram error:", e)

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

    if text == "/start":
        send_message(
            chat_id,
            "Welcome to Flight Watcher ✈️\n\n"
            "Send your route in this format:\n"
            "`KTM BKK 7 10 200`\n\n"
            "Meaning:\n"
            "Origin Destination MinDays MaxDays MaxPrice",
        )

    else:
        parts = text.split()
        if len(parts) != 5:
            send_message(chat_id, "❌ Invalid format. Try:\nKTM BKK 7 10 200")
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

        send_message(chat_id, "✅ Route added. Watching for deals!")

    return "ok", 200

# =========================
# AMADEUS TOKEN (stub)
# =========================
def get_amadeus_token():
    # You already know this works — keep stub here
    return "DUMMY_TOKEN"

# =========================
# ADAPTIVE WATCHER LOOP
# =========================
def adaptive_watcher():
    print("✈️ Adaptive watcher running")

    while True:
        time.sleep(1800)  # 30 min baseline

        with lock:
            routes_snapshot = ROUTES.copy()

        for route in routes_snapshot:
            try:
                print(
                    f"Checking {route['origin']} → {route['destination']} "
                    f"{route['min_days']}-{route['max_days']} days"
                )

                # 🔮 Real Amadeus logic plugs here
                # price = fetch_price(...)

                # if price <= route["max_price"]:
                #     send_message(route["chat_id"], f"🔥 DEAL FOUND: ${price}")

                route["last_checked"] = datetime.utcnow().isoformat()

            except Exception as e:
                print("Watcher error:", e)

# =========================
# STARTUP
# =========================
if __name__ == "__main__":
    print("🚀 Starting Flight Watcher service")

    watcher_thread = threading.Thread(target=adaptive_watcher, daemon=True)
    watcher_thread.start()

    app.run(host="0.0.0.0", port=PORT)
