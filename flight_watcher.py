from flask import Flask
import threading
import requests
import schedule
import time
from datetime import datetime, timedelta
import os

# ======================
# CONFIG FROM ENV (SAFE)
# ======================

KIWI_API_KEY = os.getenv("KIWI_API_KEY")
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")

ORIGIN = "KTM"
DESTINATION = "BKK"
CURRENCY = "USD"

CHECK_EVERY_MINUTES = 3
DAYS_AHEAD = 180

PRICE_THRESHOLD = {
    "economy": 180,
    "premium_economy": 300,
    "business": 700,
    "first": 1200
}

HEADERS = {"apikey": KIWI_API_KEY}
KIWI_URL = "https://api.tequila.kiwi.com/v2/search"
TG_URL = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"

seen_alerts = set()

# ======================
# TELEGRAM ALERT
# ======================
def send_telegram(message):
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        print("Telegram not configured")
        return

    payload = {
        "chat_id": TELEGRAM_CHAT_ID,
        "text": message,
        "disable_web_page_preview": True
    }
    requests.post(TG_URL, data=payload, timeout=10)


# ======================
# SEARCH LOGIC
# ======================
def search_flights():
    date_from = datetime.now().strftime("%d/%m/%Y")
    date_to = (datetime.now() + timedelta(days=DAYS_AHEAD)).strftime("%d/%m/%Y")

    params = {
        "fly_from": ORIGIN,
        "fly_to": DESTINATION,
        "date_from": date_from,
        "date_to": date_to,
        "curr": CURRENCY,
        "limit": 50,
        "sort": "price",
        "one_for_city": 1
    }

    try:
        r = requests.get(KIWI_URL, headers=HEADERS, params=params, timeout=15)
        data = r.json()

        if "data" not in data:
            print("API error or rate limit")
            return

        for flight in data["data"]:
            price = flight["price"]
            cabin = flight.get("cabin_class", "economy")
            if cabin not in PRICE_THRESHOLD:
                cabin = "economy"

            if price > PRICE_THRESHOLD[cabin]:
                continue

            key = f"{cabin}_{flight['local_departure']}_{price}"
            if key in seen_alerts:
                continue

            seen_alerts.add(key)
            alert(flight, price, cabin)

    except Exception as e:
        print("Error:", e)


# ======================
# ALERT FORMAT
# ======================
def alert(flight, price, cabin):
    msg = (
        "🔥 CHEAP FLIGHT ALERT 🔥\n\n"
        f"Route: {ORIGIN} → {DESTINATION}\n"
        f"Cabin: {cabin.upper()}\n"
        f"Price: ${price}\n"
        f"Departure: {flight['local_departure']}\n\n"
        f"Book now:\n{flight['deep_link']}"
    )
    print(msg)
    send_telegram(msg)


# ======================
# MAIN LOOP
# ======================
print("✈️ Flight watcher started...")

search_flights()
schedule.every(CHECK_EVERY_MINUTES).minutes.do(search_flights)

while True:
    schedule.run_pending()
    time.sleep(1)
app = Flask(__name__)

@app.route("/")
def home():
    return "Flight watcher is running 🚀"

def run_watcher():
    print("✈️ Flight watcher started")
    while True:
        schedule.run_pending()
        time.sleep(1)

if __name__ == "__main__":
    watcher_thread = threading.Thread(target=run_watcher)
    watcher_thread.start()

    app.run(host="0.0.0.0", port=8080)
