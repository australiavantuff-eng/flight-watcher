import os
import time
import json
from datetime import datetime, timedelta

import requests
from flask import Flask, request
from telegram import Update
from telegram.ext import (
    Dispatcher, CommandHandler, CallbackContext,
    MessageHandler, Filters, ConversationHandler, CallbackQueryHandler
)
import threading

# ======================
# CONFIG FROM ENV
# ======================
AMADEUS_API_KEY = os.getenv("AMADEUS_API_KEY")
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
DEFAULT_POLL_MINUTES = 30
HIGH_FREQ_MINUTES = 5
DAILY_API_LIMIT = 2000

# ======================
# CONVERSATION STATES
# ======================
(
    ORIGIN, DESTINATION, DAYS_AHEAD,
    PRICE_ECONOMY, PRICE_PREMIUM, PRICE_BUSINESS, PRICE_FIRST
) = range(7)

# ======================
# ROUTES & STATE STORAGE
# ======================
user_routes_file = "routes.json"
seen_alerts_file = "seen_alerts.json"

if os.path.exists(user_routes_file):
    with open(user_routes_file, "r") as f:
        user_routes = json.load(f)
else:
    user_routes = {}

if os.path.exists(seen_alerts_file):
    with open(seen_alerts_file, "r") as f:
        seen_alerts = set(json.load(f))
else:
    seen_alerts = set()

# Track API usage
api_usage = {}

# ======================
# TELEGRAM FUNCTIONS
# ======================
def send_telegram(msg, chat_id):
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    payload = {"chat_id": chat_id, "text": msg, "disable_web_page_preview": True}
    try:
        requests.post(url, data=payload, timeout=10)
    except Exception as e:
        print("Telegram send error:", e)

# ======================
# FLIGHT SEARCH
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

    # Track API usage
    today_str = datetime.now().strftime("%Y-%m-%d")
    key = f"{chat_id}_{today_str}"
    api_usage[key] = api_usage.get(key, 0) + 1
    if api_usage[key] > DAILY_API_LIMIT:
        print(f"API limit reached for {chat_id} today")
        return

    try:
        r = requests.get("https://test.api.amadeus.com/v2/shopping/flight-offers",
                         headers=headers, params=params, timeout=15)
        data = r.json()
        flights = data.get("data", [])

        # Check caching
        last_price = route.get("last_price", None)
        price_changed = False

        for flight in flights:
            price = float(flight.get("price", {}).get("total", 0))
            cabin = flight.get("travelerPricings", [{}])[0].get(
                "fareDetailsBySegment", [{}])[0].get("cabin", "economy").lower()
            if cabin not in thresholds:
                cabin = "economy"

            key_alert = f"{origin}_{dest}_{cabin}_{price}_{flight.get('id')}"
            if key_alert in seen_alerts:
                continue

            if price <= thresholds[cabin]:
                seen_alerts.add(key_alert)
                msg = (
                    f"🔥 CHEAP FLIGHT ALERT 🔥\n\n"
                    f"{origin} → {dest}\n"
                    f"Cabin: {cabin.upper()}\n"
                    f"Price: ${price}\n"
                    f"Departure: {flight.get('itineraries',[{}])[0].get('segments',[{}])[0].get('departure', {}).get('at')}\n"
                    f"Book: {flight.get('id','link unavailable')}"
                )
                send_telegram(msg, chat_id)
                price_changed = True

        # Update route cache
        if flights:
            min_price = min([float(f.get("price", {}).get("total", 0)) for f in flights])
            route["last_price"] = min_price
            route["last_checked"] = datetime.now().timestamp()
            # Burst mode if cheap flight detected
            if price_changed:
                route["polling_interval"] = HIGH_FREQ_MINUTES
            else:
                route["polling_interval"] = DEFAULT_POLL_MINUTES

        # Persist seen alerts
        with open(seen_alerts_file, "w") as f:
            json.dump(list(seen_alerts), f)

    except Exception as e:
        print("Flight search error:", e)

# ======================
# ADAPTIVE POLLING LOOP
# ======================
def run_watcher():
    print("✈️ Adaptive Flight Watcher Started")
    while True:
        for user_id, routes in user_routes.items():
            for route in routes:
                last_checked = route.get("last_checked", 0)
                interval = route.get("polling_interval", DEFAULT_POLL_MINUTES)
                if datetime.now().timestamp() - last_checked >= interval * 60:
                    search_flights_for_route(route)
        time.sleep(60)

# ======================
# TELEGRAM CONVERSATION HANDLERS
# ======================
def start(update: Update, context: CallbackContext):
    update.message.reply_text("Welcome! Enter the origin airport code (e.g., KTM):")
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
        "chat_id": update.message.chat_id,
        "last_price": None,
        "last_checked": 0,
        "polling_interval": DEFAULT_POLL_MINUTES
    }
    user_routes.setdefault(user_id, []).append(route)
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

# ======================
# FLASK + TELEGRAM WEBHOOK SETUP
# ======================
app = Flask(__name__)

@app.route("/")
def home():
    return "Flight watcher running 🚀"

@app.route(f"/webhook/{TELEGRAM_BOT_TOKEN}", methods=["POST"])
def telegram_webhook():
    update = Update.de_json(request.get_json(force=True), bot=None)
    dispatcher.process_update(update)
    return "ok"

# ======================
# ENTRY POINT
# ======================
if __name__ == "__main__":
    from telegram import Bot
    from telegram.ext import Dispatcher

    bot = Bot(token=TELEGRAM_BOT_TOKEN)
    dispatcher = Dispatcher(bot, None, workers=0, use_context=True)

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
        fallbacks=[CommandHandler("cancel", cancel)]
    )

    dispatcher.add_handler(conv_handler)
    dispatcher.add_handler(CommandHandler("status", status))

    threading.Thread(target=run_watcher, daemon=True).start()
    port = int(os.environ.get("PORT", 10000))
    app.run(host="0.0.0.0", port=port)
