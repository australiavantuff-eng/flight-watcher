from flask import Flask
import threading
import requests
import schedule
import time
from datetime import datetime, timedelta
import os

# ======================
# CONFIG FROM ENV
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
    print("🔍 Checking flights...")
    date_from = datetime.now().strftime("%d/%m_
