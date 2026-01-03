import os
import time
import json
import threading
import requests
from datetime import datetime, timedelta

from flask import Flask, request
from telegram import Update, Bot
from telegram.ext import (
    Dispatcher, CommandHandler, MessageHandler,
    ConversationHandler, Filters, CallbackContext
)

# ======================
# ENV CONFIG
# ======================
AMADEUS_API_KEY = os.getenv("AMADEUS_API_KEY")
AMADEUS_API_SECRET = os.getenv("AMADEUS_API_SECRET")
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")

DEFAULT_POLL_MINUTES = 35
HIGH_FREQ_MINUTES = 5
DAILY_API_LIMIT = 2000

# ======================
# TELEGRAM STATES
# ======================
(
    ORIGIN, DESTINATION, DAYS_AHEAD,
    PRICE_ECONOMY, PRICE_PREMIUM, PRICE_BUSINESS, PRICE_FIRST
) = range(7)

# ======================
# STORAGE
# ======================
ROUTES_FILE = "routes.json"
ALERTS_FILE = "seen_alerts.json"

user_routes = json.load(open(ROUTES_FILE)) if os.path.exists(ROUTES_FILE) else {}
seen_alerts = set(json.load(open(ALERTS_FILE))) if os.path.exists(ALERTS_FILE) else set()
api_usage = {}

# ======================
# AMADEUS TOKEN HANDLING
# ======================
amadeus_token = None
amadeus_token_expiry = 0

def get_amadeus_token():
    global amadeus_token, amadeus_token_expiry

    if amadeus_token and time.time() < amadeus_token_expiry:
        return amadeus_token

    resp = requests.post(
        "https://test.api.amadeus.com/v1/security/oauth2/token",
        data={
            "grant_type": "client_credentials",
            "client_id": AMADEUS_API_KEY,
            "client_secret": AMADEUS_API_SECRET,
        },
        timeout=10,
    )

    data = resp.json()
    amadeus_token = data["access_token"]
    amadeus_token_expiry = time.time() + data["expires_in"] - 60
    return amadeus_token

# ======================
# TELEGRAM SEND
# ======================
def send_telegram(chat_id, text):
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    requests.post(url, data={"chat_id": chat_id, "text": text}, timeout=10)

# ======================
# FLIGHT SEARCH
# ======================
def search_route(route):
    token = get_amadeus_token()
    today = datetime.now().strftime("%Y-%m-%d")

    usage_key = f"{route['chat_id']}_{today}"
    api_usage[usage_key] = api_usage.get(usage_key, 0) + 1
    if api_usage[usage_key] > DAILY_API_LIMIT:
        return

    headers = {"Authorization": f"Bearer {token}"}
    params = {
        "originLocationCode": route["origin"],
        "destinationLocationCode": route["destination"],
        "departureDate": datetime.now().strftime("%Y-%m-%d"),
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
    cheapest = None
    triggered = False

    for f in flights:
        price = float(f["price"]["total"])
        cheapest = min(cheapest, price) if cheapest else price

        alert_id = f"{route['origin']}_{route['destination']}_{price}"
        if alert_id in seen_alerts:
            continue

        if price <= route["thresholds"]["economy"]:
            send_telegram(
                route["chat_id"],
                f"🔥 DEAL FOUND 🔥\n{route['origin']} → {route['destination']}\n💰 ${price}",
            )
            seen_alerts.add(alert_id)
            triggered = True

    route["last_checked"] = time.time()
    route["last_price"] = cheapest
    route["polling_interval"] = HIGH_FREQ_MINUTES if triggered else DEFAULT_POLL_MINUTES

    json.dump(list(seen_alerts), open(ALERTS_FILE, "w"))

# ======================
# ADAPTIVE WATCHER
# ======================
def watcher_loop():
    print("✈️ Adaptive watcher running")
    while True:
        for routes in user_routes.values():
            for route in routes:
                if time.time() - route.get("last_checked", 0) >= route.get(
                    "polling_interval", DEFAULT_POLL_MINUTES
                ) * 60:
                    search_route(route)
        time.sleep(60)

# ======================
# TELEGRAM CONVERSATION
# ======================
def start(update: Update, context: CallbackContext):
    update.message.reply_text("Enter origin airport code:")
    return ORIGIN

def origin(update, context):
    context.user_data["origin"] = update.message.text.upper()
    update.message.reply_text("Enter destination airport code:")
    return DESTINATION

def destination(update, context):
    context.user_data["destination"] = update.message.text.upper()
    update.message.reply_text("Days ahead to search:")
    return DAYS_AHEAD

def days_ahead(update, context):
    context.user_data["days_ahead"] = int(update.message.text)
    update.message.reply_text("Max Economy price:")
    return PRICE_ECONOMY

def price_economy(update, context):
    context.user_data["thresholds"] = {"economy": float(update.message.text)}
    user_id = str(update.message.from_user.id)

    route = {
        "origin": context.user_data["origin"],
        "destination": context.user_data["destination"],
        "days_ahead": context.user_data["days_ahead"],
        "thresholds": context.user_data["thresholds"],
        "chat_id": update.message.chat_id,
        "last_checked": 0,
        "polling_interval": DEFAULT_POLL_MINUTES,
    }

    user_routes.setdefault(user_id, []).append(route)
    json.dump(user_routes, open(ROUTES_FILE, "w"), indent=2)

    update.message.reply_text("✅ Route added!")
    return ConversationHandler.END

# ======================
# FLASK + WEBHOOK
# ======================
app = Flask(__name__)
bot = Bot(TELEGRAM_BOT_TOKEN)
dispatcher = Dispatcher(bot, None, workers=4, use_context=True)

dispatcher.add_handler(
    ConversationHandler(
        entry_points=[CommandHandler("start", start)],
        states={
            ORIGIN: [MessageHandler(Filters.text & ~Filters.command, origin)],
            DESTINATION: [MessageHandler(Filters.text & ~Filters.command, destination)],
            DAYS_AHEAD: [MessageHandler(Filters.text & ~Filters.command, days_ahead)],
            PRICE_ECONOMY: [MessageHandler(Filters.text & ~Filters.command, price_economy)],
        },
        fallbacks=[],
    )
)

@app.route("/", methods=["GET"])
def health():
    return "Flight watcher alive"

@app.route("/webhook", methods=["POST"])
def webhook():
    update = Update.de_json(request.get_json(force=True), bot)
    dispatcher.process_update(update)
    return "ok"

# ======================
# ENTRY POINT
# ======================
if __name__ == "__main__":
    threading.Thread(target=watcher_loop, daemon=True).start()
    app.run(host="0.0.0.0", port=int(os.getenv("PORT", 10000)))
