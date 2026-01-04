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
if not BOT_TOKEN:
    raise ValueError("⚠️ TELEGRAM_BOT_TOKEN not set in environment variables")

AMADEUS_KEY = os.getenv("AMADEUS_KEY")
AMADEUS_SECRET = os.getenv("AMADEUS_SECRET")

BASE_POLL_MIN = 30 * 60
BURST_POLL_MIN = 8 * 60
VOLATILITY_THRESHOLD = 0.15  # 15% drop triggers burst

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
# TELEGRAM HANDLERS
# =========================

def start(update, context):
    keyboard = [
        [InlineKeyboardButton("One-way", callback_data="oneway"),
         InlineKeyboardButton("Round-trip", callback_data="roundtrip")]
    ]
    update.message.reply_text(
        "✈️ Choose trip type:",
        reply_markup=InlineKeyboardMarkup(keyboard)
    )

def trip_type_selected(update, context):
    query = update.callback_query
    query.answer()
    user_state[query.from_user.id] = {"trip_type": query.data}
    query.message.reply_text("Enter origin airport code (e.g. KTM):")

def handle_text(update, context):
    uid = update.message.from_user.id
    text = update.message.text.strip().upper()
    state = user_state.get(uid, {})

    if "origin" not in state:
        state["origin"] = text
        update.message.reply_text("Enter destination airport code:")
    elif "destination" not in state:
        state["destination"] = text
        update.message.reply_text("Enter minimum trip duration (days):")
    elif "min_days" not in state:
        state["min_days"] = int(text)
        update.message.reply_text("Enter maximum trip duration (days):")
    elif "max_days" not in state:
        state["max_days"] = int(text)
        update.message.reply_text("Enter max acceptable price (USD):")
    else:
        state["max_price"] = int(text)
        routes.append({
            "chat_id": update.message.chat_id,
            **state,
            "last_check": 0,
            "burst": False
        })
        user_state.pop(uid)
        update.message.reply_text("✅ Route added. Watching for deals!")

    user_state[uid] = state

def trend(update, context):
    uid = update.message.from_user.id
    user_routes = [r for r in routes if r["chat_id"] == uid]
    if not user_routes:
        update.message.reply_text("No routes being tracked yet.")
        return

    msg_lines = []
    for route in user_routes:
        key_prefix = f"{route['origin']}-{route['destination']}"
        prices = [p for k, p in price_cache.items() if k.startswith(key_prefix)]
        if not prices:
            msg_lines.append(f"{route['origin']} → {route['destination']}: No price data yet.")
            continue
        avg = sum(prices) / len(prices)
        min_p = min(prices)
        max_p = max(prices)
        msg_lines.append(
            f"{route['origin']} → {route['destination']}:\n"
            f"Avg: ${avg:.2f}, Min: ${min_p:.2f}, Max: ${max_p:.2f}"
        )
    update.message.reply_text("\n\n".join(msg_lines))

# =========================
# AMADEUS HELPERS
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
    return r.json()["access_token"]

def search_flights(route, token):
    deals = []
    today = datetime.utcnow()

    for offset in range(1, 60):
        dep = today + timedelta(days=offset)
        for dur in range(route["min_days"], route["max_days"] + 1):
            ret = dep + timedelta(days=dur)
            key = f"{route['origin']}-{route['destination']}-{dep}-{ret}"
            if key in price_cache:
                continue

            params = {
                "originLocationCode": route["origin"],
                "destinationLocationCode": route["destination"],
                "departureDate": dep.strftime("%Y-%m-%d"),
                "returnDate": ret.strftime("%Y-%m-%d"),
                "adults": 1,
                "travelClass": "ECONOMY",
                "currencyCode": "USD",
            }

            headers = {"Authorization": f"Bearer {token}"}
            api_usage["calls"] += 1

            r = requests.get(
                "https://test.api.amadeus.com/v2/shopping/flight-offers",
                params=params,
                headers=headers,
            )

            if r.status_code != 200:
                continue

            data = r.json()
            if not data.get("data"):
                continue

            price = float(data["data"][0]["price"]["total"])
            price_cache[key] = price

            if price <= route["max_price"]:
                deals.append((dep, ret, price))

    return deals

# =========================
# VOLATILITY & RECOMMENDATION
# =========================

def check_volatility(route, price):
    key_prefix = f"{route['origin']}-{route['destination']}"
    recent_prices = [p for k, p in price_cache.items() if k.startswith(key_prefix)]
    if not recent_prices:
        return False, 0
    avg_price = sum(recent_prices) / len(recent_prices)
    change = (avg_price - price) / avg_price
    is_burst = change >= VOLATILITY_THRESHOLD
    return is_burst, change

def get_confidence(change):
    return min(max(change * 100, 0), 100)

def get_recommendation(change):
    if change >= VOLATILITY_THRESHOLD:
        return "💡 Book now!"
    return "⌛ Wait for better deals."

# =========================
# WATCH LOOP
# =========================

def watcher_loop():
    token = get_amadeus_token()

    while True:
        now = time.time()

        for route in routes:
            interval = BURST_POLL_MIN if route["burst"] else BASE_POLL_MIN
            if now - route["last_check"] < interval:
                continue

            route["last_check"] = now
            deals = search_flights(route, token)

            for dep, ret, price in deals:
                is_burst, change = check_volatility(route, price)
                confidence = get_confidence(change)
                recommendation = get_recommendation(change)

                msg = (
                    f"🔥 DEAL FOUND!\n\n"
                    f"{route['origin']} → {route['destination']}\n"
                    f"🛫 {dep.date()}  🛬 {ret.date()}\n"
                    f"💰 ${price}\n"
                    f"📊 Confidence: {confidence:.0f}%\n"
                    f"{recommendation}"
                )

                bot.send_message(route["chat_id"], msg)
                route["burst"] = is_burst

        time.sleep(10)

# =========================
# FLASK WEBHOOK
# =========================

@app.route("/webhook", methods=["POST"])
def webhook():
    update = Update.de_json(request.get_json(force=True), bot)
    dispatcher.process_update(update)
    return "OK"

@app.route("/")
def health():
    return "Flight watcher running", 200

# =========================
# MAIN
# =========================

dispatcher.add_handler(CommandHandler("start", start))
dispatcher.add_handler(CommandHandler("trend", trend))
dispatcher.add_handler(CallbackQueryHandler(trip_type_selected))
dispatcher.add_handler(MessageHandler(Filters.text & ~Filters.command, handle_text))

threading.Thread(target=watcher_loop, daemon=True).start()

if __name__ == "__main__":
    print("✈️ Adaptive watcher running")
    app.run(host="0.0.0.0", port=10000)
