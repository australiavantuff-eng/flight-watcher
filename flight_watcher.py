import os
import threading
import time
import json
from datetime import datetime, timedelta

import requests
from flask import Flask
from telegram import Update
from telegram.ext import (
    Updater,
    CommandHandler,
    ConversationHandler,
    MessageHandler,
    Filters,
    CallbackContext,
)

# ======================
# ENV CONFIG
# ======================
AMADEUS_API_KEY = os.getenv("AMADEUS_API_KEY")
AMADEUS_API_SECRET = os.getenv("AMADEUS_API_SECRET")
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")

CHECK_EVERY_MINUTES = int(os.getenv("CHECK_EVERY_MINUTES", 30))
PORT = int(os.getenv("PORT", 10000))

# ======================
# FILE STORAGE
# ======================
ROUTES_FILE = "routes.json"
SEEN_FILE = "seen_alerts.json"

user_routes = {}
seen_alerts = set()

if os.path.exists(ROUTES_FILE):
    with open(ROUTES_FILE) as f:
        user_routes = json.load(f)

if os.path.exists(SEEN_FILE):
    with open(SEEN_FILE) as f:
        seen_alerts = set(json.load(f))

# ======================
# TELEGRAM STATES
# ======================
(
    ORIGIN,
    DESTINATION,
    DAYS_AHEAD,
    PRICE_ECONOMY,
    PRICE_BUSINESS,
    CONFIRM,
) = range(6)

# ======================
# TELEGRAM UTIL
# ======================
def send_telegram(chat_id, text):
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    payload = {"chat_id": chat_id, "text": text}
    try:
        requests.post(url, data=payload, timeout=10)
    except Exception as e:
        print("Telegram send error:", e)

# ======================
# AMADEUS TOKEN
# ======================
def get_amadeus_token():
    r = requests.post(
        "https://test.api.amadeus.com/v1/security/oauth2/token",
        data={
            "grant_type": "client_credentials",
            "client_id": AMADEUS_API_KEY,
            "client_secret": AMADEUS_API_SECRET,
        },
        timeout=10,
    )
    return r.json().get("access_token")

# ======================
# FLIGHT SEARCH
# ======================
def search_route(route):
    token = get_amadeus_token()
    if not token:
        return

    headers = {"Authorization": f"Bearer {token}"}

    date_from = datetime.utcnow().date()
    date_to = date_from + timedelta(days=route["days_ahead"])

    params = {
        "originLocationCode": route["origin"],
        "destinationLocationCode": route["destination"],
        "departureDate": date_from.isoformat(),
        "adults": 1,
        "max": 20,
    }

    r = requests.get(
        "https://test.api.amadeus.com/v2/shopping/flight-offers",
        headers=headers,
        params=params,
        timeout=15,
    )

    flights = r.json().get("data", [])

    for f in flights:
        price = float(f["price"]["total"])
        key = f"{route['origin']}-{route['destination']}-{price}"

        if key in seen_alerts:
            continue

        if price <= route["price"]:
            seen_alerts.add(key)
            with open(SEEN_FILE, "w") as sf:
                json.dump(list(seen_alerts), sf)

            msg = (
                f"🔥 CHEAP FLIGHT FOUND 🔥\n\n"
                f"{route['origin']} → {route['destination']}\n"
                f"Price: ${price}\n"
                f"Check Amadeus or airline site"
            )
            send_telegram(route["chat_id"], msg)

# ======================
# WATCHER LOOP
# ======================
def watcher():
    print("✈️ Flight watcher started (optimized)")
    while True:
        for routes in user_routes.values():
            for route in routes:
                try:
                    search_route(route)
                except Exception as e:
                    print("Watcher error:", e)
        time.sleep(CHECK_EVERY_MINUTES * 60)

# ======================
# TELEGRAM HANDLERS
# ======================
def start(update: Update, context: CallbackContext):
    update.message.reply_text("Enter ORIGIN airport code (e.g. KTM):")
    return ORIGIN

def origin(update, context):
    context.user_data["origin"] = update.message.text.upper()
    update.message.reply_text("Enter DESTINATION airport code (e.g. BKK):")
    return DESTINATION

def destination(update, context):
    context.user_data["destination"] = update.message.text.upper()
    update.message.reply_text("Search how many days ahead? (e.g. 90)")
    return DAYS_AHEAD

def days_ahead(update, context):
    context.user_data["days"] = int(update.message.text)
    update.message.reply_text("Max price USD?")
    return PRICE_ECONOMY

def price(update, context):
    chat_id = update.message.chat_id
    route = {
        "origin": context.user_data["origin"],
        "destination": context.user_data["destination"],
        "days_ahead": context.user_data["days"],
        "price": float(update.message.text),
        "chat_id": chat_id,
    }

    user_routes.setdefault(str(chat_id), []).append(route)
    with open(ROUTES_FILE, "w") as rf:
        json.dump(user_routes, rf, indent=2)

    update.message.reply_text("✅ Route added and monitoring started")
    return ConversationHandler.END

def status(update, context):
    routes = user_routes.get(str(update.message.chat_id), [])
    if not routes:
        update.message.reply_text("No routes tracked.")
        return

    msg = "Tracked routes:\n"
    for r in routes:
        msg += f"{r['origin']} → {r['destination']} under ${r['price']}\n"
    update.message.reply_text(msg)

def cancel(update, context):
    update.message.reply_text("Cancelled.")
    return ConversationHandler.END

# ======================
# TELEGRAM START
# ======================
def start_bot():
    updater = Updater(TELEGRAM_BOT_TOKEN, use_context=True)
    dp = updater.dispatcher

    conv = ConversationHandler(
        entry_points=[CommandHandler("start", start)],
        states={
            ORIGIN: [MessageHandler(Filters.text & ~Filters.command, origin)],
            DESTINATION: [MessageHandler(Filters.text & ~Filters.command, destination)],
            DAYS_AHEAD: [MessageHandler(Filters.text & ~Filters.command, days_ahead)],
            PRICE_ECONOMY: [MessageHandler(Filters.text & ~Filters.command, price)],
        },
        fallbacks=[CommandHandler("cancel", cancel)],
    )

    dp.add_handler(conv)
    dp.add_handler(CommandHandler("status", status))

    updater.start_polling()
    print("🤖 Telegram bot started")

# ======================
# FLASK APP
# ======================
app = Flask(__name__)

@app.route("/")
def home():
    return "Flight watcher running 🚀"

# ======================
# MAIN
# ======================
if __name__ == "__main__":
    threading.Thread(target=watcher, daemon=True).start()
    threading.Thread(target=start_bot, daemon=True).start()
    app.run(host="0.0.0.0", port=PORT)
