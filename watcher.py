import json
import requests
import os
from datetime import datetime, timedelta
from dateutil.relativedelta import relativedelta

TELEGRAM_TOKEN = os.environ["TELEGRAM_TOKEN"]
AMADEUS_TOKEN = os.environ["AMADEUS_TOKEN"]

ROUTES_FILE = "routes.json"
CACHE_FILE = "price_cache.json"

TELEGRAM_API = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"


def send_telegram(chat_id, text):
    requests.post(TELEGRAM_API, json={
        "chat_id": chat_id,
        "text": text,
        "parse_mode": "Markdown"
    })


def load_json(path, default):
    if not os.path.exists(path):
        return default
    with open(path, "r") as f:
        return json.load(f)


def save_json(path, data):
    with open(path, "w") as f:
        json.dump(data, f, indent=2)


def get_dates(min_days, max_days, ahead_days):
    today = datetime.utcnow().date()
    end = today + timedelta(days=ahead_days)

    results = []
    d = today + timedelta(days=1)

    while d <= end:
        for stay in range(min_days, max_days + 1):
            return_date = d + timedelta(days=stay)
            if return_date <= end:
                results.append((d, return_date))
        d += timedelta(days=1)

    return results


def search_amadeus(origin, destination, depart, return_date):
    url = "https://test.api.amadeus.com/v2/shopping/flight-offers"
    headers = {"Authorization": f"Bearer {AMADEUS_TOKEN}"}

    params = {
        "originLocationCode": origin,
        "destinationLocationCode": destination,
        "departureDate": depart.isoformat(),
        "returnDate": return_date.isoformat(),
        "adults": 1,
        "currencyCode": "USD",
        "max": 5
    }

    r = requests.get(url, headers=headers, params=params, timeout=20)
    r.raise_for_status()
    return r.json().get("data", [])


def main():
    routes = load_json(ROUTES_FILE, [])
    cache = load_json(CACHE_FILE, {})

    for route in routes:
        key = f"{route['origin']}-{route['destination']}"
        last_price = cache.get(key, float("inf"))

        dates = get_dates(
            route["min_days"],
            route["max_days"],
            route.get("search_ahead_days", 120)
        )

        best_price = last_price
        best_trip = None

        for depart, ret in dates[:30]:  # adaptive cap per run
            try:
                offers = search_amadeus(
                    route["origin"],
                    route["destination"],
                    depart,
                    ret
                )
            except Exception:
                continue

            for offer in offers:
                price = float(offer["price"]["grandTotal"])
                if price < best_price:
                    best_price = price
                    best_trip = (depart, ret)

        if best_trip and best_price <= route["max_price"]:
            send_telegram(
                route["chat_id"],
                f"✈️ *DEAL FOUND!*\n\n"
                f"{route['origin']} → {route['destination']} → {route['origin']}\n"
                f"📅 {best_trip[0]} – {best_trip[1]}\n"
                f"💵 ${best_price}\n"
                f"🔥 Below your limit!"
            )

        cache[key] = best_price

    save_json(CACHE_FILE, cache)


if __name__ == "__main__":
    main()
