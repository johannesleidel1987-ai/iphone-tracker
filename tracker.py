import json
import os
import re
import requests
from bs4 import BeautifulSoup

# ================= KONFIGURATION =================
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "DEIN_BOT_TOKEN_HIER")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "DEINE_CHAT_ID_HIER")

MAX_PRICE = 2450.00
MIN_PRICE = 1850.00      # Schutz vor Hüllen, Dummys und Fake-Inseraten bei Sofort-Kaufen
MIN_PRICE_AUCTION = 500.00  # Niedrigerer Startfilter für laufende Auktionen (< 24h)

# Unterstützte Herstellernummern für iPhone Duo 256GB Sternenweiß
MPNS = ["MK2D4ZD/A", "MK2D4QL/A", "MK2D4SX/A"]

# Postleitzahlen für Apple-API (deckt Augsburg, beide Münchner Stores und Stuttgart ab)
APPLE_SEARCH_LOCATIONS = ["86150", "70173"]
# Strikter Filter: Nur diese Städte/Stores dürfen einen Alarm auslösen
ALLOWED_APPLE_STORES = ["augsburg", "city-galerie", "münchen", "rosenstraße", "oez", "olympia", "stuttgart", "sindelfingen"]

SEEN_FILE = "seen_offers.json"
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36",
    "Accept-Language": "de-DE,de;q=0.9"
}

BLACKLIST = [
    "hülle", "case", "folie", "panzerglas", "defekt", "suche", "tausche",
    "verpackung", "ovp leer", "leere ovp", "dummy", "nachthimmel", "night sky", "reparatur"
]
# =================================================

def load_seen():
    if os.path.exists(SEEN_FILE):
        with open(SEEN_FILE, "r", encoding="utf-8") as f:
            return set(json.load(f))
    return set()

def save_seen(seen):
    with open(SEEN_FILE, "w", encoding="utf-8") as f:
        json.dump(list(seen), f)

def send_telegram(message):
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    payload = {
        "chat_id": TELEGRAM_CHAT_ID,
        "text": message,
        "parse_mode": "HTML",
        "disable_web_page_preview": False
    }
    try:
        requests.post(url, json=payload, timeout=10)
    except Exception as e:
        print(f"Telegram-Fehler: {e}")

def parse_price(price_str):
    # Bei Preisspannen ("1.900,00 € bis 2.300,00 €") nur den ersten Preis nehmen
    first_part = price_str.split("bis")[0]
    clean = re.sub(r"[^\d,]", "", first_part).replace(",", ".")
    try:
        return float(clean)
    except ValueError:
        return None

def check_apple_store_pickup(seen):
    """Prüft die offizielle Apple Fulfillment API strikt für Augsburg, München und Stuttgart."""
    parts_query = "&".join([f"parts.{i}={ requests.utils.quote(mpn) }" for i, mpn in enumerate(MPNS)])

    for plz in APPLE_SEARCH_LOCATIONS:
        url = f"https://www.apple.com/de/shop/fulfillment-messages?pl=true&mts.0=regular&{parts_query}&location={plz}"
        try:
            r = requests.get(url, headers=HEADERS, timeout=12)
            if r.status_code != 200:
                continue

            data = r.json()
            stores = data.get("body", {}).get("content", {}).get("pickupMessage", {}).get("stores", [])

            for store in stores:
                store_name = store.get("storeName", "")
                city = store.get("city", "")
                combined_location = f"{store_name} {city}".lower()

                # Strikter Filter auf Augsburg, München und Stuttgart
                if not any(allowed in combined_location for allowed in ALLOWED_APPLE_STORES):
                    continue

                parts_avail = store.get("partsAvailability", {})
                for mpn in MPNS:
                    part_info = parts_avail.get(mpn, {})
                    pickup_display = part_info.get("pickupDisplay", "unavailable")
                    pickup_quote = part_info.get("pickupSearchQuote", "Sofort verfügbar")

                    if pickup_display == "available":
                        offer_id = f"apple_{store_name}_{mpn}_{pickup_quote}"
                        if offer_id not in seen:
                            msg = (
                                f"🍏 <b>APPLE STORE PICKUP VERFÜGBAR!</b>\n"
                                f"Store: <b>Apple {store_name} ({city})</b>\n"
                                f"Modell: iPhone Duo 256 GB Sternenweiß ({mpn})\n"
                                f"Status: <b>{pickup_quote}</b>\n"
                                f"Preis: 2.299,00 €\n"
                                f"👉 <a href='https://www.apple.com/de/shop/buy-iphone/iphone-duo'>Jetzt direkt reservieren</a>"
                            )
                            send_telegram(msg)
                            seen.add(offer_id)
        except Exception as e:
            print(f"Apple API Check Fehler ({plz}): {e}")

def check_geizhals_shops(seen):
    """Prüft alle deutschen Online-Shops via Geizhals auf sofortigen Lagerbestand <= 2400€."""
    url = "https://geizhals.de/apple-iphone-duo-256gb-sternenweiss-a3936870.html?t=v&vl=de&va=b"
    try:
        r = requests.get(url, headers=HEADERS, timeout=15)
        soup = BeautifulSoup(r.text, "html.parser")
        offers = soup.select(".offer")

        for offer in offers:
            shop_elem = offer.select_one(".merchant__logo-caption") or offer.select_one(".offer__merchant")
            price_elem = offer.select_one(".gh_price")
            avail_elem = offer.select_one(".offer__delivery-time")
            link_elem = offer.select_one("a.offer__clickout")

            if not (shop_elem and price_elem and avail_elem):
                continue

            shop = shop_elem.get_text(strip=True)
            price = parse_price(price_elem.get_text(strip=True))
            avail_text = avail_elem.get_text(" ", strip=True).lower()
            link = "https://geizhals.de" + link_elem["href"] if link_elem and link_elem.get("href") else url

            is_in_stock = any(k in avail_text for k in ["lagernd", "sofort", "1-2 werktage", "1-3 werktage", "2-3 werktage", "auf lager"])
            is_delayed = any(b in avail_text for b in ["wochen", "monat", "2027", "unbestimmt", "vorbestell"])

            if price and MIN_PRICE <= price <= MAX_PRICE and is_in_stock and not is_delayed:
                offer_id = f"shop_{shop}_{price}"
                if offer_id not in seen:
                    msg = (
                        f"🛒 <b>SHOP-BESTAND GEFUNDEN: {shop}</b>\n"
                        f"Modell: iPhone Duo 256 GB Sternenweiß\n"
                        f"Preis: <b>{price:.2f} €</b>\n"
                        f"Status: {avail_elem.get_text(' ', strip=True)}\n"
                        f"👉 <a href='{link}'>Direkt zum Shop</a>"
                    )
                    send_telegram(msg)
                    seen.add(offer_id)
    except Exception as e:
        print(f"Geizhals-Check Fehler: {e}")

def check_kleinanzeigen(seen):
    """Prüft Kleinanzeigen.de auf neue Inserate zwischen 1.850 € und 2.400 €."""
    url = f"https://www.kleinanzeigen.de/s-preis:{int(MIN_PRICE)}:{int(MAX_PRICE)}/iphone-duo-256-sternenweiss/k0"
    try:
        r = requests.get(url, headers=HEADERS, timeout=15)
        soup = BeautifulSoup(r.text, "html.parser")
        articles = soup.select("article.aditem")

        for art in articles:
            ad_id = art.get("data-adid")
            title_elem = art.select_one(".text-module-begin a")
            price_elem = art.select_one(".aditem-main--middle--price-shipping--price")
            loc_elem = art.select_one(".aditem-main--top--left")

            if not (ad_id and title_elem and price_elem):
                continue

            title = title_elem.get_text(strip=True)
            if any(bad in title.lower() for bad in BLACKLIST):
                continue

            price = parse_price(price_elem.get_text(strip=True))
            if not price or not (MIN_PRICE <= price <= MAX_PRICE):
                continue

            link = "https://www.kleinanzeigen.de" + title_elem["href"]
            location = loc_elem.get_text(" ", strip=True) if loc_elem else "Unbekannt"
            offer_id = f"ka_{ad_id}"

            if offer_id not in seen:
                msg = (
                    f"📦 <b>KLEINANZEIGEN-TREFFER (≤ 2.400 €)</b>\n"
                    f"Titel: {title}\n"
                    f"Preis: <b>{price:.2f} €</b>\n"
                    f"Ort: {location}\n"
                    f"⚠️ <i>Hinweis: Nur Abholung mit Original-Rechnung!</i>\n"
                    f"👉 <a href='{link}'>Zur Anzeige</a>"
                )
                send_telegram(msg)
                seen.add(offer_id)
    except Exception as e:
        print(f"Kleinanzeigen-Check Fehler: {e}")

def check_ebay(seen):
    """
    Prüft eBay.de getrennt nach:
    1. Sofort-Kaufen (LH_BIN=1) bis max. 2.400 €
    2. Auktionen (LH_Auction=1) mit Restlaufzeit < 24 Stunden (_ftrt=901&_ftrv=24) und aktuellem Preis <= 2.400 €
    """
    base_query = "iphone+duo+256gb+%28sternenwei%C3%9F%2C+wei%C3%9F%2C+star+white%2C+MK2D4ZD%2FA%29+-h%C3%BClle+-case+-folie"

    search_modes = [
        {
            "type": "SOFORT-KAUFEN",
            "emoji": "🏷️",
            "min_p": MIN_PRICE,
            # LH_BIN=1 (Sofort-Kaufen), _sop=10 (Neu eingestellt)
            "url": f"https://www.ebay.de/sch/i.html?_nkw={base_query}&_sacat=9355&LH_BIN=1&_udlo={int(MIN_PRICE)}&_udhi={int(MAX_PRICE)}&_sop=10"
        },
        {
            "type": "AUKTION (< 24h Restzeit)",
            "emoji": "⏳",
            "min_p": MIN_PRICE_AUCTION,
            # LH_Auction=1 (Auktion), LH_Time=1&_ftrt=901&_ftrv=24 (Endet innerhalb von 24 Stunden), _sop=1 (Bald endend zuerst)
            "url": f"https://www.ebay.de/sch/i.html?_nkw={base_query}&_sacat=9355&LH_Auction=1&LH_Time=1&_ftrt=901&_ftrv=24&_udlo={int(MIN_PRICE_AUCTION)}&_udhi={int(MAX_PRICE)}&_sop=1"
        }
    ]

    for mode in search_modes:
        try:
            r = requests.get(mode["url"], headers=HEADERS, timeout=15)
            soup = BeautifulSoup(r.text, "html.parser")
            items = soup.select(".s-item__wrapper")

            for item in items:
                title_elem = item.select_one(".s-item__title")
                price_elem = item.select_one(".s-item__price")
                link_elem = item.select_one("a.s-item__link")
                time_elem = item.select_one(".s-item__time-left")

                if not (title_elem and price_elem and link_elem):
                    continue

                title = re.sub(r"^Neues Angebot\s*", "", title_elem.get_text(strip=True), flags=re.IGNORECASE)
                if "Shop on eBay" in title or any(bad in title.lower() for bad in BLACKLIST):
                    continue

                price = parse_price(price_elem.get_text(strip=True))
                if not price or not (mode["min_p"] <= price <= MAX_PRICE):
                    continue

                time_left = time_elem.get_text(strip=True) if time_elem else "Sofort verfügbar"

                # Zusätzliche Absicherung bei Auktionen: Falls eBay doch ein Element mit "Tage" / "d" einstreut, herausfiltern
                if mode["type"].startswith("AUKTION") and time_elem:
                    t_low = time_left.lower()
                    if "tag" in t_low or re.search(r"\b\d+\s*t\b", t_low):
                        continue

                clean_link = link_elem["href"].split("?")[0]
                item_id = clean_link.split("/")[-1]
                offer_id = f"ebay_{mode['type'][:3]}_{item_id}"

                if offer_id not in seen:
                    msg = (
                        f"{mode['emoji']} <b>EBAY {mode['type']}</b>\n"
                        f"Titel: {title}\n"
                        f"Aktueller Preis: <b>{price:.2f} €</b>\n"
                        f"Restlaufzeit: <b>{time_left}</b>\n"
                        f"👉 <a href='{clean_link}'>Zum eBay-Angebot</a>"
                    )
                    send_telegram(msg)
                    seen.add(offer_id)
        except Exception as e:
            print(f"eBay-Check Fehler ({mode['type']}): {e}")

if __name__ == "__main__":
    seen_offers = load_seen()
    check_apple_store_pickup(seen_offers)
    check_geizhals_shops(seen_offers)
    check_kleinanzeigen(seen_offers)
    check_ebay(seen_offers)
    save_seen(seen_offers)
