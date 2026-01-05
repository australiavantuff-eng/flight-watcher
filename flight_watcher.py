import os
import time
import json
import threading
from datetime import datetime, timedelta
from flask import Flask, request, jsonify
import requests

# ================= CONFIG =================

TELEGRAM_TOKEN = os.getenv(
    "TELEGRAM_TOKEN",
    "8016721347:AAH96Ikn-rN4Dr4ALrxp8uttOUBw9NIPkPU"
)
AMADEUS_KEY = os.getenv("AMADEUS_KEY")
AMADEUS_SECRET = os.getenv("AMADEUS_SECRET")

BASE_URL = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}"
WEBHOOK_PATH = "/webhook"

CHECK_INTERVAL_NORMAL = 35 * 60
CHECK_INTERVAL_BURST = 10 * 60
BURST_WINDOW = 3 * 60 * 60

# ================= APP =================

app = Flask(__name__)

user_states = {}
routes = []
price_cache = {}
burst_until = {}

# ================= TELEGRAM HELPERS =================

def send_message(chat_id, text, reply_markup=None):
    payload = {
        "chat_id": chat_id,
        "text": text
    }
    if reply_markup:
        payload["reply_markup"] = reply_markup
    requests.post(f"{BASE_URL}/sendMessage", json=payload, timeout=10)

# ================= AMADEUS =================

_amadeus_token = None
_amadeus_expiry = 0

def get_amadeus_token():
    global _amadeus_token, _amadeus_expiry
    if _amadeus_token and time.time() < _amadeus_expiry:
        return _amadeus_token

    r = requests.post(
        "https://test.api.amadeus.com/v1/security/oauth2/token",
        data={
            "grant_type": "client_credentials",
            "client_id": AMADEUS_KEY,
            "client_secret": AMADEUS_SECRET,
        },
        timeout=10,
    )
    data = r.json()
    _amadeus_token = data["access_token"]
    _amadeus_expiry = time.time() + data["expires_in"] - 60
    return _amadeus_token

# ================= SEARCH LOGIC =================

def search_route(route):
    token = get_amadeus_token()
    headers = {"Authorization": f"Bearer {token}"}

    today = datetime.utcnow().date()
    horizon = route["horizon"]

    cheapest = {}

    for d in range(horizon):
        dep = today + timedelta(days=d)
        for dur in range(route["min_days"], route["max_days"] + 1):
            ret = dep + timedelta(days=dur)
            key = f"{route['origin']}-{route['dest']}-{dep}-{ret}"

            if key in price_cache:
                continue

            params = {
                "originLocationCode": route["origin"],
                "destinationLocationCode": route["dest"],
                "departureDate": dep.isoformat(),
                "returnDate": ret.isoformat(),
                "adults": 1,
                "currencyCode": "USD",
                "travelClass": "ECONOMY",
                "max": 10
            }

            r = requests.get(
                "https://test.api.amadeus.com/v2/shopping/flight-offers",
                headers=headers,
                params=params,
                timeout=15
            )

            if r.status_code != 200:
                continue

            data = r.json().get("data", [])
            for offer in data:
                price = float(offer["price"]["total"])
                cabin = offer["travelerPricings"][0]["fareDetailsBySegment"][0]["cabin"]
                cheapest.setdefault(cabin, []).append(price)

            price_cache[key] = True

    return cheapest

# ================= DEAL MONITOR =================

def monitor():
    while True:
        now = time.time()
        for route in routes:
            cid = route["chat_id"]
            cheapest = search_route(route)

            for cabin, prices in cheapest.items():
                best = min(prices)
                if best <= route["max_price"]:
                    send_message(
                        cid,
                        f"🔥 DEAL FOUND!\n"
                        f"{route['origin']} ↔ {route['dest']}\n"
                        f"Cabin: {cabin}\n"
                        f"Trip: {route['min_days']}-{route['max_days']} days\n"
                        f"Price: ${best}"
                    )
                    burst_until[cid] = now + BURST_WINDOW

        sleep_time = CHECK_INTERVAL_BURST if any(
            now < burst_until.get(r["chat_id"], 0) for r in routes
        ) else CHECK_INTERVAL_NORMAL

        time.sleep(sleep_time)

# ================= TELEGRAM WEBHOOK =================

@app.route(WEBHOOK_PATH, methods=["POST"])
def webhook():
    update = request.json
    msg = update.get("message")
    cb = update.get("callback_query")

    if cb:
        chat_id = cb["message"]["chat"]["id"]
        data = cb["data"]
        user_states[chat_id] = {"trip_type": data}
        send_message(chat_id, "Enter origin airport code (e.g. KTM):")
        return jsonify(ok=True)

    if not msg:
        return jsonify(ok=True)

    chat_id = msg["chat"]["id"]
    text = msg.get("text", "")

    state = user_states.get(chat_id, {})

    if text == "/start":
        send_message(
            chat_id,
            "Choose trip type:",
            reply_markup={
                "inline_keyboard": [[
                    {"text": "Round Trip", "callback_data": "round"}
                ]]
            }
        )
        return jsonify(ok=True)

    if "origin" not in state:
        state["origin"] = text.upper()
        user_states[chat_id] = state
        send_message(chat_id, "Enter destination airport code:")
        return jsonify(ok=True)

    if "dest" not in state:
        state["dest"] = text.upper()
        send_message(chat_id, "Enter minimum trip duration (days):")
        return jsonify(ok=True)

    if "min_days" not in state:
        state["min_days"] = int(text)
        send_message(chat_id, "Enter maximum trip duration (days):")
        return jsonify(ok=True)

    if "max_days" not in state:
        state["max_days"] = int(text)
        send_message(chat_id, "How far ahead should I search? (days from today)")
        return jsonify(ok=True)

    if "horizon" not in state:
        state["horizon"] = int(text)
        send_message(chat_id, "Enter max acceptable price (USD):")
        return jsonify(ok=True)

    if "max_price" not in state:
        state["max_price"] = float(text)
        routes.append({
            "chat_id": chat_id,
            **state
        })
        user_states.pop(chat_id, None)
        send_message(chat_id, "✅ Route added. Watching for deals!")
        return jsonify(ok=True)

    return jsonify(ok=True)

@app.route("/")
def home():
    return "Flight watcher running"

# ================= START =================

if __name__ == "__main__":
    threading.Thread(target=monitor, daemon=True).start()
    print("✈️ Adaptive watcher running")
    app.run(host="0.0.0.0", port=10000)
