import os
import threading
import time
import json
from datetime import datetime, timedelta

import requests
from flask import Flask
from telegram import (
    Update, InlineKeyboardButton, InlineKeyboardMarkup
)
from telegram.ext import (
    Updater, CommandHandler, CallbackQueryHandler,
    ConversationHandler, CallbackContext, MessageHandler, Filters
)

# ======================
# CONFIG FROM ENV
# ======================
AMADEUS_API_KEY = os.getenv("AMADEUS_API_KEY")
AMADEUS_API_SECRET = os.getenv("AMADEUS_API_SECRET")
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
CHECK_EVERY_MINUTES = 3

# ======================
# CONVERSATION STATES
# ======================
(
    ORIGIN, DESTINATION, DAYS_AHEAD,
    PRICE_ECONOMY, PRICE_PREMIUM, PRICE_BUSINESS, PRICE_FIRST, CONFIRM
) = range(8)

# ======================
# ROUTES STORAGE
# ======================
user_routes_file = "routes.json"
seen_alerts_file = "seen_alerts.json"

# Load persisted routes
if os.path.exists(user_routes_file):
    with open(user_routes_file, "r") as f:
        user_routes = json.load(f)
else:
    user_routes = {}

# Load persisted seen alerts
if os.path.exists(seen_alerts_file):
    with open(seen_alerts_file, "r") as f:
        seen_alerts = set(json.load(f))
else:
    seen_alerts = set()

# ======================
# TELEGRAM ALERT
# ======================
def send_telegram(msg, chat_id):
    if not TELEGRAM_BOT_TOKEN or not chat_id:
        return
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    payload = {"chat_id": chat_id, "text": msg, "disable_web_page_preview": True}
    try:
        requests.post(url, data=payload, timeout=10)
    except Exception as e:
        print("Telegram send error:", e)

# ======================
# FLIGHT SEARCH FUNCTION
# ======================
def search_flights_for_route(route):
    origin = route["origin"]
    dest = route["destination"]
    days_ahead = route["days_ahead"]
    thresholds = route["thresholds"]
    chat_id = route["chat_id"]

    date_from = datetime.now().strftime("%Y-%m-%d")
    date_to = (datetime.now() + timedelta(days=days_ahead)).strftime("%Y-%m-%d")

    headers = {"Authorization": f"Bearer {AMADEUS_API_KEY}"}
    params = {
        "originLocationCode": origin,
        "destinationLocationCode": dest,
        "departureDate": date_from,
        "returnDate": date_to,
        "adults": 1,
        "max": 50
    }

    try:
        # NOTE: Replace with real Amadeus API endpoint
        r = requests.get("https://test.api.amadeus.com/v2/shopping/flight-offers",
                         headers=headers, params=params, timeout=15)
        data = r.json()
        flights = data.get("data", [])

        for flight in flights:
            price = float(flight.get("price", {}).get("total", 0))
            cabin = flight.get("travelerPricings", [{}])[0].get(
                "fareDetailsBySegment", [{}])[0].get("cabin", "economy").lower()
            if cabin not in thresholds:
                cabin = "economy"

            key = f"{origin}_{dest}_{cabin}_{price}_{flight.get('id')}"
            if key in seen_alerts:
                continue

            if price <= thresholds[cabin]:
                seen_alerts.add(key)
                # Persist seen alerts
                with open(seen_alerts_file, "w") as f:
                    json.dump(list(seen_alerts), f)
                msg = (
                    f"🔥 CHEAP FLIGHT ALERT 🔥\n\n"
                    f"{origin} → {dest}\n"
                    f"Cabin: {cabin.upper()}\n"
                    f"Price: ${price}\n"
                    f"Departure: {flight.get('itineraries',[{}])[0].get('segments',[{}])[0].get('departure', {}).get('at')}\n"
                    f"Book: {flight.get('id','link unavailable')}"
                )
                send_telegram(msg, chat_id)

    except Exception as e:
        print("Flight search error:", e)

# ======================
# BACKGROUND WATCHER THREAD
# ======================
def run_watcher():
    print("✈️ Flight watcher started (background)")
    while True:
        try:
            for user_id, routes in user_routes.items():
                for route in routes:
                    search_flights_for_route(route)
        except Exception as e:
            print("Watcher thread error:", e)
        time.sleep(CHECK_EVERY_MINUTES * 60)

# ======================
# TELEGRAM BOT HANDLERS
# ======================
def start(update: Update, context: CallbackContext):
    update.message.reply_text("Welcome! Let's add a new flight route.\nEnter the origin airport code (e.g., KTM):")
    return ORIGIN

def origin(update: Update, context: CallbackContext):
    context.user_data["origin"] = update.message.text.strip().upper()
    update.message.reply_text("Enter the destination airport code (e.g., BKK):")
    return DESTINATION

def destination(update: Update, context: CallbackContext):
    context.user_data["destination"] = update.message.text.strip().upper()
    update.message.reply_text("Enter number of days ahead to search (e.g., 90):")
    return DAYS_AHEAD

def days_ahead(update: Update, context: CallbackContext):
    context.user_data["days_ahead"] = int(update.message.text.strip())
    update.message.reply_text("Set max price for Economy (USD):")
    return PRICE_ECONOMY

def price_economy(update: Update, context: CallbackContext):
    context.user_data.setdefault("thresholds", {})["economy"] = float(update.message.text.strip())
    update.message.reply_text("Set max price for Premium Economy (USD):")
    return PRICE_PREMIUM

def price_premium(update: Update, context: CallbackContext):
    context.user_data["thresholds"]["premium_economy"] = float(update.message.text.strip())
    update.message.reply_text("Set max price for Business (USD):")
    return PRICE_BUSINESS

def price_business(update: Update, context: CallbackContext):
    context.user_data["thresholds"]["business"] = float(update.message.text.strip())
    update.message.reply_text("Set max price for First Class (USD):")
    return PRICE_FIRST

def price_first(update: Update, context: CallbackContext):
    context.user_data["thresholds"]["first"] = float(update.message.text.strip())
    user_id = str(update.message.from_user.id)
    route = {
        "origin": context.user_data["origin"],
        "destination": context.user_data["destination"],
        "days_ahead": context.user_data["days_ahead"],
        "thresholds": context.user_data["thresholds"],
        "chat_id": update.message.chat_id
    }
    user_routes.setdefault(user_id, []).append(route)
    # Persist routes
    with open(user_routes_file, "w") as f:
        json.dump(user_routes, f, indent=2)
    update.message.reply_text(f"✅ Route {route['origin']} → {route['destination']} added!")
    return ConversationHandler.END

def cancel(update: Update, context: CallbackContext):
    update.message.reply_text("❌ Operation cancelled.")
    return ConversationHandler.END

def status(update: Update, context: CallbackContext):
    user_id = str(update.message.from_user.id)
    routes = user_routes.get(user_id, [])
    if not routes:
        update.message.reply_text("No routes tracked yet.")
        return
    msg = "Tracked routes:\n"
    for r in routes:
        msg += f"{r['origin']} → {r['destination']}, {r['days_ahead']} days ahead, thresholds: {r['thresholds']}\n"
    update.message.reply_text(msg)

def remove(update: Update, context: CallbackContext):
    user_id = str(update.message.from_user.id)
    user_routes[user_id] = []
    with open(user_routes_file, "w") as f:
        json.dump(user_routes, f, indent=2)
    update.message.reply_text("All routes removed.")

# ======================
# FLASK + TELEGRAM INIT
# ======================
app = Flask(__name__)

@app.route("/")
def home():
    return "Flight watcher is running 🚀"

def start_telegram_bot():
    updater = Updater(TELEGRAM_BOT_TOKEN, use_context=True)
    dp = updater.dispatcher

    conv_handler = ConversationHandler(
        entry_points=[CommandHandler("start", start)],
        states={
            ORIGIN: [MessageHandler(Filters.text & ~Filters.command, origin)],
            DESTINATION: [MessageHandler(Filters.text & ~Filters.command, destination)],
            DAYS_AHEAD: [MessageHandler(Filters.text & ~Filters.command, days_ahead)],
            PRICE_ECONOMY: [MessageHandler(Filters.text & ~Filters.command, price_economy)],
            PRICE_PREMIUM: [MessageHandler(Filters.text & ~Filters.command, price_premium)],
            PRICE_BUSINESS: [MessageHandler(Filters.text & ~Filters.command, price_business)],
            PRICE_FIRST: [MessageHandler(Filters.text & ~Filters.command, price_first)],
        },
        fallbacks=[CommandHandler("cancel", cancel)],
    )

    dp.add_handler(conv_handler)
    dp.add_handler(CommandHandler("status", status))
    dp.add_handler(CommandHandler("remove", remove))

    updater.start_polling()
    print("🤖 Telegram bot started")
    updater.idle()

# ======================
# ENTRY POINT
# ======================
if __name__ == "__main__":
    threading.Thread(target=run_watcher, daemon=True).start()
    threading.Thread(target=start_telegram_bot, daemon=True).start()

    port = int(os.environ.get("PORT", 10000))
    app.run(host="0.0.0.0", port=port)
