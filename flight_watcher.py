import os
import time
import threading
import requests
from datetime import datetime, timedelta
from flask import Flask, request
from telegram import Bot, Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Dispatcher,
    CommandHandler,
    MessageHandler,
    Filters,
    CallbackQueryHandler,
)

# =========================
# CONFIG
# =========================

BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
AMADEUS_KEY = os.getenv("AMADEUS_API_KEY")
AMADEUS_SECRET = os.getenv("AMADEUS_API_SECRET")

BASE_POLL_SEC = 35 * 60
BURST_POLL_SEC = 8 * 60
MAX_API_CALLS_PER_CYCLE = 40   # hard safety cap
VOLATILITY_THRESHOLD = 0.15

AMADEUS_URL = "https://test.api.amadeus.com/v2/shopping/flight-offers"

CABIN_CLASSES = [
    "ECONOMY",
    "PREMIUM_ECONOMY",
    "BUSINESS",
    "FIRST"
]

app = Flask(__name__)
bot = Bot(BOT_TOKEN)
dispatcher = Dispatcher(bot, None, workers=1, use_context=True)

# =========================
# STATE
# =========================

user_state = {}
routes = []
price_cache = {}
api_usage = {"calls": 0}

# =========================
# TELEGRAM FLOW
# =========================

def start(update, context):
    kb = [[
        InlineKeyboardButton("One-way", callback_data="oneway"),
        InlineKeyboardButton("Round-trip", callback_data="roundtrip")
    ]]
    update.message.reply_text(
        "✈️ Choose trip type:",
        reply_markup=InlineKeyboardMarkup(kb)
    )

def trip_type_selected(update, context):
    q = update.callback_query
    q.answer()
    user_state[q.from_user.id] = {"trip_type": q.data}
    q.message.reply_text("Enter origin airport code (e.g. KTM):")

def handle_text(update, context):
    uid = update.message.from_user.id
    text = update.message.text.strip().upper()
    state = user_state.get(uid, {})

    if "origin" not in state:
        state["origin"] = text
        update.message.reply_text("Enter destination airport code:")
    elif "destination" not in state:
        state["destination"] = text
        if state["trip_type"] == "roundtrip":
            update.message.reply_text("Enter minimum trip duration (days):")
        else:
            state["min_days"] = 0
            state["max_days"] = 0
            update.message.reply_text("Enter max acceptable price (USD):")
    elif "min_days" not in state:
        state["min_days"] = int(text)
        update.message.reply_text("Enter maximum trip duration (days):")
    elif "max_days" not in state:
        state["max_days"] = int(text)
        update.message.reply_text("Enter max acceptable price (USD):")
    else:
        state["max_price"] = int(text)

        # Deduplicate routes
        route_key = (
            update.message.chat_id,
            state["origin"],
            state["destination"],
            state["trip_type"],
            state["min_days"],
            state["max_days"]
        )
        for r in routes:
            if r["key"] == route_key:
                update.message.reply_text("⚠️ This route is already being tracked.")
                user_state.pop(uid)
                return

        routes.append({
            "key": route_key,
            "chat_id": update.message.chat_id,
            **state,
            "last_check": 0,
            "burst": False
        })

        user_state.pop(uid)
        update.message.reply_text("✅ Route added. Watching for deals!")

    user_state[uid] = state

# =========================
# AMADEUS
# =========================

def get_amadeus_token():
    r = requests.post(
        "https://test.api.amadeus.com/v1/security/oauth2/token",
        data={
            "grant_type": "client_credentials",
            "client_id": AMADEUS_KEY,
            "client_secret": AMADEUS_SECRET,
        },
    )
    r.raise_for_status()
    return r.json()["access_token"]

def search_flights(route, token):
    deals = []
    headers = {"Authorization": f"Bearer {token}"}
    today = datetime.utcnow()

    checked = 0
    for offset in [7, 14, 21, 30]:  # smart sampling
        dep = today + timedelta(days=offset)

        durations = [0] if route["trip_type"] == "oneway" else range(route["min_days"], route["max_days"] + 1)

        for dur in durations:
            ret = dep + timedelta(days=dur) if dur else None

            for cabin in CABIN_CLASSES:
                if checked >= MAX_API_CALLS_PER_CYCLE:
                    return deals

                key = f"{route['origin']}-{route['destination']}-{dep.date()}-{ret}-{cabin}"
                if key in price_cache:
                    continue

                params = {
                    "originLocationCode": route["origin"],
                    "destinationLocationCode": route["destination"],
                    "departureDate": dep.strftime("%Y-%m-%d"),
                    "adults": 1,
                    "travelClass": cabin,
                    "currencyCode": "USD",
                }
                if ret:
                    params["returnDate"] = ret.strftime("%Y-%m-%d")

                api_usage["calls"] += 1
                checked += 1

                r = requests.get(AMADEUS_URL, params=params, headers=headers)
                if r.status_code != 200:
                    continue

                data = r.json().get("data", [])
                if not data:
                    continue

                price = float(data[0]["price"]["total"])
                price_cache[key] = price

                if price <= route["max_price"]:
                    deals.append((dep, ret, price, cabin))

    return deals

# =========================
# WATCHER LOOP
# =========================

def watcher_loop():
    token = get_amadeus_token()
    print("🚀 Adaptive watcher started")

    while True:
        now = time.time()
        for route in routes:
            interval = BURST_POLL_SEC if route["burst"] else BASE_POLL_SEC
            if now - route["last_check"] < interval:
                continue

            route["last_check"] = now
            deals = search_flights(route, token)

            for dep, ret, price, cabin in deals:
                msg = (
                    f"🔥 DEAL FOUND\n\n"
                    f"{route['origin']} → {route['destination']}\n"
                    f"🛫 {dep.date()}"
                )
                if ret:
                    msg += f"\n🛬 {ret.date()}"
                msg += (
                    f"\n💺 {cabin}"
                    f"\n💰 ${price}"
                )
                bot.send_message(route["chat_id"], msg)
                route["burst"] = True

        time.sleep(10)

# =========================
# FLASK
# =========================

@app.route("/webhook", methods=["POST"])
def webhook():
    update = Update.de_json(request.get_json(force=True), bot)
    dispatcher.process_update(update)
    return "OK"

@app.route("/")
def health():
    return "Flight watcher running", 200

@app.route("/favicon.ico")
def favicon():
    return "", 204

# =========================
# MAIN
# =========================

dispatcher.add_handler(CommandHandler("start", start))
dispatcher.add_handler(CallbackQueryHandler(trip_type_selected))
dispatcher.add_handler(MessageHandler(Filters.text & ~Filters.command, handle_text))

threading.Thread(target=watcher_loop, daemon=True).start()

if __name__ == "__main__":
    print("✈️ Adaptive watcher running")
    app.run(host="0.0.0.0", port=10000)
