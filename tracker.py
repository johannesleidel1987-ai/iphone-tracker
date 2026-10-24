import json
import os
import re
import requests
from bs4 import BeautifulSoup

# ================= KONFIGURATION =================
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "DEIN_BOT_TOKEN_HIER")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "DEINE_CHAT_ID_HIER")

MAX_PRICE = 2400.00
MIN_PRICE = 1850.00  # Schutz vor Hüllen, Dummys und offensichtlichen Fake-Inseraten
MPN = "MK2D4ZD/A"    # Offizielle Apple-Teilenummer für 256GB Sternenweiß (DE)
PLZ_APPLE_PICKUP = "86150"  # Prüft Apple Stores im Umkreis (Augsburg & München)

SEEN_FILE = "seen_offers.json"
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36",
    "Accept-Language": "de-DE,de;q=0.9"
}

BLACKLIST = ["hülle", "case", "folie", "panzerglas", "defekt", "suche", "tausche", "verpackung", "ovp leer", "dummy", "nachthimmel", "night sky"]
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
    # Entfernt Punkte (Tausendertrennzeichen) und wandelt Komma in Punkt um
    clean = re.sub(r"[^\d,]", "", price_str).replace(",", ".")
    try:
        return float(clean)
    except ValueError:
        return None

def check_apple_store_pickup(seen):
    """Prüft die offizielle Apple Fulfillment API auf Same-Day-Pickup in Augsburg & München."""
    url = f"https://www.apple.com/de/shop/fulfillment-messages?pl=true&mts.0=regular&parts.0={MPN}&location={PLZ_APPLE_PICKUP}"
    try:
        r = requests.get(url, headers=HEADERS, timeout=12)
        if r.status_code == 200:
            data = r.json()
            stores = data.get("body", {}).get("content", {}).get("pickupMessage", {}).get("stores", [])
            for store in stores:
                store_name = store.get("storeName", "Apple Store")
                part_info = store.get("partsAvailability", {}).get(MPN, {})
                pickup_display = part_info.get("pickupDisplay", "unavailable")
                pickup_quote = part_info.get("pickupSearchQuote", "")

                if pickup_display == "available":
                    offer_id = f"apple_{store_name}_{pickup_quote}"
                    if offer_id not in seen:
                        msg = (
                            f"🍏 <b>APPLE STORE ALARM ({store_name})</b>\n"
                            f"Modell: iPhone Duo 256 GB Sternenweiß\n"
                            f"Status: <b>{pickup_quote} (Sofort abholbar!)</b>\n"
                            f"Preis: 2.299,00 €\n"
                            f"👉 <a href='https://www.apple.com/de/shop/buy-iphone/iphone-duo'>Jetzt in der Apple Store App reservieren</a>"
                        )
                        send_telegram(msg)
                        seen.add(offer_id)
    except Exception as e:
        print(f"Apple API Check übersprungen: {e}")

def check_geizhals_shops(seen):
    """Prüft alle deutschen Online-Shops (MediaMarkt, Saturn, Cyberport, Galaxus, o2 etc.) auf Lagerbestand <= 2400€."""
    # Geizhals-Produktseite für Apple iPhone Duo 256GB Sternenweiß (ID: a3936870) mit Filter "Lagernd / Kurzfristig"
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

            # Prüfe ob sofort/kurzfristig lieferbar (schließt "ab 09.11.", "Januar", "unbestimmt" aus)
            is_in_stock = any(k in avail_text for k in ["lagernd", "sofort", "1-2 werktage", "1-3 werktage", "2-3 werktage", "auf lager"])
            is_delayed = any(b in avail_text for b in ["wochen", "monat", "2027", "unbestimmt", "vorbestell"])

            if price and MIN_PRICE <= price <= MAX_PRICE and is_in_stock and not is_delayed:
                offer_id = f"shop_{shop}_{price}"
                if offer_id not in seen:
                    msg = (
                        f"🛒 <b>SHOP-BESTAND GEFUNDEN: {shop}</b>\n"
                        f"Modell: iPhone Duo 256 GB Sternenweiß\n"
                        f"Preis: <b>{price:.2f} €</b>\n"
                        f"Lieferstatus: {avail_elem.get_text(' ', strip=True)}\n"
                        f"👉 <a href='{link}'>Direkt zum Angebot</a>"
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
            title_lower = title.lower()
            if any(bad in title_lower for bad in BLACKLIST):
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
                    f"⚠️ <i>Hinweis: Bei Privatkauf nur Abholung mit Original-Rechnung!</i>\n"
                    f"👉 <a href='{link}'>Zur Anzeige</a>"
                )
                send_telegram(msg)
                seen.add(offer_id)
    except Exception as e:
        print(f"Kleinanzeigen-Check Fehler: {e}")

def check_ebay(seen):
    """Prüft eBay.de Sofort-Kaufen-Angebote (Neu/Sonstige) zwischen 1.850 € und 2.400 €."""
    url = (
        f"https://www.ebay.de/sch/i.html?_nkw=iphone+duo+256gb+%28sternenwei%C3%9F%2C+wei%C3%9F%2C+MK2D4ZD%2FA%29+-h%C3%BClle+-case"
        f"&_sacat=9355&LH_BIN=1&_udlo={int(MIN_PRICE)}&_udhi={int(MAX_PRICE)}&_sop=10"
    )
    try:
        r = requests.get(url, headers=HEADERS, timeout=15)
        soup = BeautifulSoup(r.text, "html.parser")
        items = soup.select(".s-item__wrapper")

        for item in items:
            title_elem = item.select_one(".s-item__title")
            price_elem = item.select_one(".s-item__price")
            link_elem = item.select_one("a.s-item__link")

            if not (title_elem and price_elem and link_elem):
                continue

            title = title_elem.get_text(strip=True)
            if "Shop on eBay" in title or any(bad in title.lower() for bad in BLACKLIST):
                continue

            price = parse_price(price_elem.get_text(strip=True))
            if not price or not (MIN_PRICE <= price <= MAX_PRICE):
                continue

            clean_link = link_elem["href"].split("?")[0]
            offer_id = f"ebay_{clean_link.split('/')[-1]}"

            if offer_id not in seen:
                msg = (
                    f"🏷️ <b>EBAY SOFORT-KAUFEN GEFUNDEN</b>\n"
                    f"Titel: {title}\n"
                    f"Preis: <b>{price:.2f} €</b>\n"
                    f"👉 <a href='{clean_link}'>Zum eBay-Angebot</a>"
                )
                send_telegram(msg)
                seen.add(offer_id)
    except Exception as e:
        print(f"eBay-Check Fehler: {e}")

if __name__ == "__main__":
    seen_offers = load_seen()
    check_apple_store_pickup(seen_offers)
    check_geizhals_shops(seen_offers)
    check_kleinanzeigen(seen_offers)
    check_ebay(seen_offers)
    save_seen(seen_offers)
