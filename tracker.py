import html
import json
import os
import re
import time
from datetime import datetime
from zoneinfo import ZoneInfo

import requests
from bs4 import BeautifulSoup

# ================= KONFIGURATION =================
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "")

MAX_PRICE = 2400.00
MIN_PRICE = 1850.00         # Schutz vor Hüllen, Dummys und Fake-Inseraten bei Sofort-Kaufen
MIN_PRICE_AUCTION = 500.00  # Niedrigerer Startfilter für laufende Auktionen (< 24h)

# Apple-Store-Abfrage: nur die deutsche Teilenummer für iPhone Duo 256GB Sternenweiß.
# Ausländische Suffixe (QL/A, SX/A) kennt apple.com/de nicht und können die ganze Antwort leeren.
MPNS = ["MK2D4ZD/A"]

# Postleitzahlen für Apple-API: München (Rosenstraße, OEZ), Augsburg, Sindelfingen
APPLE_SEARCH_LOCATIONS = ["80331", "86150", "71065"]
# Abgleich gegen Store-Name und Stadt. Gefundene Stores inkl. Store-Nummer stehen im Log.
ALLOWED_APPLE_STORES = ["augsburg", "city-galerie", "rosenstraße", "rosenstrasse", "oez", "olympia",
                        "sindelfingen", "breuningerland", "münchen", "muenchen", "munich"]
# Diese Städte müssen in der Antwort auftauchen, sonst Health-Fehler (erkennt geänderte Store-Namen)
EXPECTED_APPLE_CITIES = ["münchen", "augsburg", "sindelfingen"]

# Tägliche Restock-Zusammenfassung (Ortszeit Berlin)
try:
    TZ = ZoneInfo("Europe/Berlin")
except Exception:  # Windows ohne tzdata-Paket: Ortszeit des Rechners verwenden
    TZ = None
DIGEST_HOUR = 21                        # erster Lauf ab 21:00 sendet die Tagesmeldung
RESTOCK_LOG_RETENTION_DAYS = 14

# Kleinanzeigen: breite Suche, Farbe wird nicht im Suchbegriff erzwungen (Blacklist filtert Fremdfarben)
KA_QUERY = "iphone-duo-256"

# Titel muss alle Begriffe enthalten (Kleinanzeigen + eBay)
REQUIRED_TITLE_TERMS = ["duo", "256"]

STATE_FILE = "state.json"
LEGACY_SEEN_FILE = "seen_offers.json"   # wird beim ersten Lauf einmalig übernommen
SEEN_RETENTION_DAYS = 30

HEALTH_FAIL_THRESHOLD = 6               # Fehlläufe in Folge bis zur Warnung (= 30 Min bei 5-Min-Takt)
HEALTH_WARN_INTERVAL = 24 * 3600        # max. eine Warnung pro Quelle und Tag

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36",
    "Accept-Language": "de-DE,de;q=0.9"
}

BLACKLIST = [
    "hülle", "case", "folie", "panzerglas", "defekt", "suche", "tausche",
    "verpackung", "ovp leer", "leere ovp", "dummy", "nachthimmel", "night sky", "reparatur"
]
# =================================================

SESSION = requests.Session()
SESSION.headers.update(HEADERS)


def esc(value):
    return html.escape(str(value), quote=True)


# ---------------- State ----------------

def load_state():
    state = {}
    if os.path.exists(STATE_FILE):
        with open(STATE_FILE, "r", encoding="utf-8") as f:
            state = json.load(f)
    elif os.path.exists(LEGACY_SEEN_FILE):
        with open(LEGACY_SEEN_FILE, "r", encoding="utf-8") as f:
            legacy = json.load(f)
        now = time.time()
        state = {"seen": {offer_id: now for offer_id in legacy}}
    state.setdefault("seen", {})     # offer_id -> Zeitstempel (Kleinanzeigen, eBay)
    state.setdefault("active", {})   # Kanal -> aktuell verfügbare Treffer (Apple, Geizhals)
    state.setdefault("health", {})   # Quelle -> Fehlerzähler
    state.setdefault("apple_status", {})  # Store|MPN -> zuletzt beobachtet verfügbar (bool)
    state.setdefault("restock_log", [])   # [{ts, store, city, mpn, event: "in"/"out"}]
    state.setdefault("last_digest", "")   # Datum (YYYY-MM-DD) der letzten Tagesmeldung
    return state


def save_state(state):
    cutoff = time.time() - SEEN_RETENTION_DAYS * 86400
    state["seen"] = {k: ts for k, ts in state["seen"].items() if ts >= cutoff}
    with open(STATE_FILE, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False)


# ---------------- Telegram & Health ----------------

def send_telegram(message):
    """Sendet eine Nachricht. Gibt True nur bei bestätigtem Versand zurück."""
    if not (TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID):
        print("Telegram nicht konfiguriert. Nachricht:\n" + message)
        return False
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    payload = {
        "chat_id": TELEGRAM_CHAT_ID,
        "text": message,
        "parse_mode": "HTML",
        "disable_web_page_preview": False
    }
    try:
        r = requests.post(url, json=payload, timeout=10)
        if r.ok and r.json().get("ok"):
            return True
        print(f"Telegram-Fehler {r.status_code}: {r.text[:300]}")
    except Exception as e:
        print(f"Telegram-Fehler: {e}")
    return False


def record_health(state, source, ok, detail=""):
    h = state["health"].setdefault(source, {"fails": 0, "last_warn": 0})
    now = time.time()
    if ok:
        if h["fails"] >= HEALTH_FAIL_THRESHOLD and h["last_warn"]:
            send_telegram(f"✅ <b>Tracker: {esc(source)} funktioniert wieder</b>")
        h["fails"] = 0
        h["last_warn"] = 0
        return
    h["fails"] += 1
    print(f"[{source}] Fehlschlag #{h['fails']}: {detail}")
    if h["fails"] >= HEALTH_FAIL_THRESHOLD and now - h["last_warn"] >= HEALTH_WARN_INTERVAL:
        msg = (
            f"⚠️ <b>Tracker-Warnung: {esc(source)}</b>\n"
            f"{h['fails']} Fehlläufe in Folge – Quelle liefert keine auswertbaren Daten "
            f"(Blockade oder Layout-Änderung).\n"
            f"Letzter Fehler: {esc(detail)}"
        )
        if send_telegram(msg):
            h["last_warn"] = now


def alert_once(state, offer_id, message):
    """Für Einzelinserate: genau ein Alarm pro offer_id, gespeichert nur nach erfolgreichem Versand."""
    if offer_id in state["seen"]:
        return
    if send_telegram(message):
        state["seen"][offer_id] = time.time()


# ---------------- Helfer ----------------

def fetch(url, timeout=15):
    r = SESSION.get(url, timeout=timeout)
    print(f"GET {url[:100]} -> HTTP {r.status_code}")
    return r


def parse_price(price_str):
    first_part = price_str.split("bis")[0]
    clean = re.sub(r"[^\d,]", "", first_part).replace(",", ".")
    try:
        return float(clean)
    except ValueError:
        return None


def title_ok(title):
    t = title.lower()
    return all(term in t for term in REQUIRED_TITLE_TERMS) and not any(bad in t for bad in BLACKLIST)


# ---------------- Apple ----------------

def check_apple_store_pickup(state):
    """Prüft die Apple Fulfillment API. Alarm nur beim Wechsel nicht verfügbar -> verfügbar."""
    parts_query = "&".join([f"parts.{i}={requests.utils.quote(mpn)}" for i, mpn in enumerate(MPNS)])
    observed = {}
    errors = []
    seen_cities = set()

    for plz in APPLE_SEARCH_LOCATIONS:
        url = f"https://www.apple.com/de/shop/retail/pickup-message?pl=true&mts.0=regular&{parts_query}&location={plz}"
        try:
            r = fetch(url, timeout=12)
            if r.status_code != 200:
                errors.append(f"PLZ {plz}: HTTP {r.status_code}")
                continue
            b = r.json().get("body", {})
            stores = b.get("stores") or b.get("content", {}).get("pickupMessage", {}).get("stores", [])
            if not stores:
                errors.append(f"PLZ {plz}: keine Stores in der Antwort")
                continue

            for store in stores:
                store_name = store.get("storeName", "")
                city = store.get("city", "")
                store_number = store.get("storeNumber", "")
                print(f"  Apple-Store: {store_name} ({city}) [{store_number}]")

                haystack = f"{store_name} {city}".lower()
                if not any(allowed in haystack for allowed in ALLOWED_APPLE_STORES):
                    continue
                seen_cities.add(city.lower())

                parts_avail = store.get("partsAvailability", {})
                for mpn in MPNS:
                    part_info = parts_avail.get(mpn, {})
                    key = f"{store_number or store_name}|{mpn}"
                    if key in observed:
                        continue  # Store bereits über andere PLZ erfasst
                    observed[key] = {
                        "available": part_info.get("pickupDisplay") == "available",
                        "store": store_name,
                        "city": city,
                        "mpn": mpn,
                        "quote": part_info.get("pickupSearchQuote", "Verfügbar"),
                    }
        except Exception as e:
            errors.append(f"PLZ {plz}: {e}")

    missing = [c for c in EXPECTED_APPLE_CITIES if not any(c in s for s in seen_cities)]
    if missing and not errors:
        errors.append(f"Erwartete Stores fehlen: {', '.join(missing)}")
    record_health(state, "Apple", not errors, "; ".join(errors))

    log_restock_events(state, observed)

    # Nur beobachtete Stores aktualisieren – ein Fehlschlag löscht keinen bestehenden Status
    active = state["active"].setdefault("apple", {})
    for key, info in observed.items():
        if not info["available"]:
            active.pop(key, None)
            continue
        if active.get(key):
            continue
        msg = (
            f"🍏 <b>APPLE STORE PICKUP VERFÜGBAR!</b>\n"
            f"Store: <b>Apple {esc(info['store'])} ({esc(info['city'])})</b>\n"
            f"Modell: iPhone Duo 256 GB Sternenweiß ({esc(info['mpn'])})\n"
            f"Status: <b>{esc(info['quote'])}</b>\n"
            f"👉 <a href='https://www.apple.com/de/shop/buy-iphone/iphone-duo'>Jetzt direkt reservieren</a>"
        )
        if send_telegram(msg):
            active[key] = True


def log_restock_events(state, observed):
    """Protokolliert Wechsel verfügbar/nicht verfügbar je Store für die Tagesmeldung."""
    status = state["apple_status"]
    log = state["restock_log"]
    now = time.time()
    for key, info in observed.items():
        was = status.get(key, False)
        if info["available"] != was:
            log.append({
                "ts": now, "key": key, "store": info["store"], "city": info["city"],
                "mpn": info["mpn"], "event": "in" if info["available"] else "out",
            })
        status[key] = info["available"]
    cutoff = now - RESTOCK_LOG_RETENTION_DAYS * 86400
    state["restock_log"] = [e for e in log if e["ts"] >= cutoff]


def _hm(ts):
    return datetime.fromtimestamp(ts, TZ).strftime("%H:%M")


def send_daily_restock_digest(state):
    """Einmal täglich ab DIGEST_HOUR: alle Apple-Restocks des Tages je Store mit Uhrzeit."""
    now_local = datetime.now(TZ)
    today = now_local.date().isoformat()
    if now_local.hour < DIGEST_HOUR or state["last_digest"] == today:
        return

    day_start = datetime(now_local.year, now_local.month, now_local.day, tzinfo=TZ).timestamp()
    by_store = {}
    for e in sorted(state["restock_log"], key=lambda e: e["ts"]):
        if e["ts"] < day_start:
            continue
        label = f"Apple {e['store']} ({e['city']})"
        windows = by_store.setdefault(label, [])
        if e["event"] == "in":
            windows.append([e["ts"], None])
        elif windows and windows[-1][1] is None:
            windows[-1][1] = e["ts"]
        else:
            windows.append([None, e["ts"]])  # war schon vor Mitternacht verfügbar

    # Stores, die den ganzen Tag ohne Wechsel verfügbar waren
    for e in reversed(state["restock_log"]):
        label = f"Apple {e['store']} ({e['city']})"
        if state["apple_status"].get(e["key"]) and e["event"] == "in" and label not in by_store:
            by_store[label] = [[None, None]]

    lines = [f"📊 <b>Apple Restocks heute ({now_local.strftime('%d.%m.%Y')})</b>",
             "iPhone Duo 256 GB Sternenweiß"]
    if not by_store:
        lines.append("Heute kein Bestand in München, Augsburg oder Sindelfingen.")
    for label, windows in by_store.items():
        lines.append(f"\n<b>{esc(label)}</b>")
        for start, end in windows:
            if start and end:
                lines.append(f"• {_hm(start)}–{_hm(end)} Uhr ({round((end - start) / 60)} Min)")
            elif start:
                lines.append(f"• ab {_hm(start)} Uhr, noch verfügbar")
            elif not end:
                lines.append("• ganztägig verfügbar")
            else:
                lines.append(f"• seit Vortag verfügbar, bis {_hm(end)} Uhr")
    lines.append("\n<i>Zeiten auf ±5 Min genau (Prüftakt).</i>")

    if send_telegram("\n".join(lines)):
        state["last_digest"] = today


# ---------------- Geizhals ----------------

def check_geizhals_shops(state):
    """Prüft deutsche Online-Shops via Geizhals. Alarm bei neuem Bestand oder Preissenkung je Shop."""
    url = "https://geizhals.de/apple-iphone-duo-256gb-sternenweiss-a3936870.html?t=v&vl=de&va=b"
    try:
        r = fetch(url)
    except Exception as e:
        record_health(state, "Geizhals", False, str(e))
        return
    if r.status_code != 200:
        record_health(state, "Geizhals", False, f"HTTP {r.status_code}")
        return

    soup = BeautifulSoup(r.text, "html.parser")
    offers = soup.select(".offer")
    if not offers:
        record_health(state, "Geizhals", False, "Keine Angebote geparst")
        return
    record_health(state, "Geizhals", True)

    current = {}
    for offer in offers:
        shop_elem = offer.select_one(".merchant__logo-caption") or offer.select_one(".offer__merchant")
        price_elem = offer.select_one(".gh_price")
        avail_elem = offer.select_one(".offer__delivery-time")
        link_elem = offer.select_one("a.offer__clickout")

        if not (shop_elem and price_elem and avail_elem):
            continue

        shop = shop_elem.get_text(strip=True)
        price = parse_price(price_elem.get_text(strip=True))
        avail_display = avail_elem.get_text(" ", strip=True)
        avail_text = avail_display.lower()
        link = "https://geizhals.de" + link_elem["href"] if link_elem and link_elem.get("href") else url

        is_in_stock = any(k in avail_text for k in ["lagernd", "sofort", "1-2 werktage", "1-3 werktage", "2-3 werktage", "auf lager"])
        is_delayed = any(b in avail_text for b in ["wochen", "monat", "2027", "unbestimmt", "vorbestell"])

        if price and MIN_PRICE <= price <= MAX_PRICE and is_in_stock and not is_delayed:
            if shop not in current or price < current[shop]["price"]:
                current[shop] = {"price": price, "avail": avail_display, "link": link}

    active = state["active"].setdefault("geizhals", {})
    for shop, info in current.items():
        prev_price = active.get(shop)
        if prev_price is not None and info["price"] >= prev_price:
            active[shop] = info["price"]
            continue
        headline = "PREIS GESUNKEN" if prev_price is not None else "SHOP-BESTAND GEFUNDEN"
        msg = (
            f"🛒 <b>{headline}: {esc(shop)}</b>\n"
            f"Modell: iPhone Duo 256 GB Sternenweiß\n"
            f"Preis: <b>{info['price']:.2f} €</b>\n"
            f"Status: {esc(info['avail'])}\n"
            f"👉 <a href='{esc(info['link'])}'>Direkt zum Shop</a>"
        )
        if send_telegram(msg):
            active[shop] = info["price"]

    for shop in list(active):
        if shop not in current:
            del active[shop]


# ---------------- billiger.de ----------------

BILLIGER_URL = "https://www.billiger.de/products/5941011354-apple-iphone-duo-256-gb-sternenweiss"


def billiger_in_stock(delivery):
    """True nur bei eindeutig kurzfristiger Lieferbarkeit (sofort, Lager oder max. 5 Werktage)."""
    d = delivery.lower()
    if not d or any(b in d for b in ["woche", "monat", "unbestimmt", "vorbestell", "nicht lieferbar",
                                     "ausverkauft", "anfang", "mitte", "ende", "2027"]):
        return False
    if any(k in d for k in ["sofort", "auf lager", "lagernd", "vorrätig"]):
        return True
    m = re.search(r"(\d+)\s*(?:-\s*(\d+))?\s*werktag", d)
    return bool(m) and int(m.group(2) or m.group(1)) <= 5


def check_billiger(state):
    """Prüft die Angebotszeilen auf billiger.de. Alarm nur bei Preis im Rahmen UND kurzfristiger Lieferzeit
    (neuer Shop oder Preissenkung)."""
    try:
        r = fetch(BILLIGER_URL)
    except Exception as e:
        record_health(state, "billiger.de", False, str(e))
        return
    if r.status_code != 200:
        record_health(state, "billiger.de", False, f"HTTP {r.status_code}")
        return

    soup = BeautifulSoup(r.text, "html.parser")
    rows = soup.select("[data-offer-row]")
    if not rows:
        record_health(state, "billiger.de", False, "Keine Angebotszeilen (data-offer-row) gefunden")
        return
    record_health(state, "billiger.de", True)

    current = {}
    for row in rows:
        text = row.get_text(" | ", strip=True)
        m = re.search(r"Verkäufer:\s*\|?\s*([^|]+)", text) or re.search(r"€ bei ([^|]+)", text)
        if m:
            seller = m.group(1).strip()
        else:
            logo = row.select_one("img[alt]")
            seller = logo["alt"].strip() if logo and logo["alt"].strip() else f"Shop {row.get('data-shop-id', '?')}"
        prices = [parse_price(p) for p in re.findall(r"(\d{1,3}(?:\.\d{3})*,\d{2})\s*€", text)]
        price = next((p for p in prices if p and p >= 100), None)  # Versandkosten überspringen
        dm = re.search(r"Lieferzeit:\s*\|?\s*([^|]+)", text)
        delivery = dm.group(1).strip() if dm else ""
        stock = billiger_in_stock(delivery)
        print(f"  billiger.de: {seller} | {price} € | {delivery or 'keine Lieferzeit'} | "
              f"{'lieferbar' if stock else 'nicht lieferbar'}")
        if price and MIN_PRICE <= price <= MAX_PRICE and stock:
            if seller not in current or price < current[seller]["price"]:
                current[seller] = {"price": price, "delivery": delivery}

    active = state["active"].setdefault("billiger", {})
    for seller, info in current.items():
        prev = active.get(seller)
        if prev is not None and info["price"] >= prev:
            active[seller] = info["price"]
            continue
        headline = "PREIS GESUNKEN" if prev is not None else "LIEFERBAR"
        msg = (
            f"💶 <b>BILLIGER.DE – {headline}: {esc(seller)}</b>\n"
            f"Modell: iPhone Duo 256 GB Sternenweiß\n"
            f"Preis: <b>{info['price']:.2f} €</b>\n"
            f"Lieferzeit: <b>{esc(info['delivery'])}</b>\n"
            f"👉 <a href='{BILLIGER_URL}'>Zu billiger.de</a>"
        )
        if send_telegram(msg):
            active[seller] = info["price"]

    for seller in list(active):
        if seller not in current:
            del active[seller]


# ---------------- Kleinanzeigen ----------------

def check_kleinanzeigen(state):
    """Prüft Kleinanzeigen.de auf neue Inserate zwischen MIN_PRICE und MAX_PRICE."""
    url = f"https://www.kleinanzeigen.de/s-preis:{int(MIN_PRICE)}:{int(MAX_PRICE)}/{KA_QUERY}/k0"
    try:
        r = fetch(url)
    except Exception as e:
        record_health(state, "Kleinanzeigen", False, str(e))
        return
    if r.status_code != 200:
        record_health(state, "Kleinanzeigen", False, f"HTTP {r.status_code}")
        return

    soup = BeautifulSoup(r.text, "html.parser")
    articles = soup.select("article.aditem")
    structure_ok = bool(articles) or soup.select_one("#srchrslt-content, #srchrslt-adtable") is not None
    if not structure_ok:
        record_health(state, "Kleinanzeigen", False, "Ergebnisliste nicht gefunden")
        return
    record_health(state, "Kleinanzeigen", True)

    for art in articles:
        ad_id = art.get("data-adid")
        title_elem = art.select_one(".text-module-begin a")
        price_elem = art.select_one(".aditem-main--middle--price-shipping--price")
        loc_elem = art.select_one(".aditem-main--top--left")

        if not (ad_id and title_elem and price_elem):
            continue

        title = title_elem.get_text(strip=True)
        if not title_ok(title):
            continue

        price = parse_price(price_elem.get_text(strip=True))
        if not price or not (MIN_PRICE <= price <= MAX_PRICE):
            continue

        link = "https://www.kleinanzeigen.de" + title_elem["href"]
        location = loc_elem.get_text(" ", strip=True) if loc_elem else "Unbekannt"

        msg = (
            f"📦 <b>KLEINANZEIGEN-TREFFER (≤ {MAX_PRICE:.0f} €)</b>\n"
            f"Titel: {esc(title)}\n"
            f"Preis: <b>{price:.2f} €</b>\n"
            f"Ort: {esc(location)}\n"
            f"⚠️ <i>Hinweis: Nur Abholung mit Original-Rechnung!</i>\n"
            f"👉 <a href='{esc(link)}'>Zur Anzeige</a>"
        )
        alert_once(state, f"ka_{ad_id}", msg)


# ---------------- eBay ----------------

def _first(item, selectors):
    for sel in selectors:
        elem = item.select_one(sel)
        if elem:
            return elem
    return None


def check_ebay(state):
    """
    Prüft eBay.de getrennt nach:
    1. Sofort-Kaufen (LH_BIN=1) bis MAX_PRICE
    2. Auktionen (LH_Auction=1) mit Restlaufzeit < 24 Stunden und aktuellem Preis <= MAX_PRICE
    Unterstützt das neue Karten-Layout (.s-card) und das alte Layout (.s-item).
    """
    base_query = "iphone+duo+256gb+%28sternenwei%C3%9F%2C+wei%C3%9F%2C+star+white%2C+MK2D4ZD%2FA%29+-h%C3%BClle+-case+-folie"

    search_modes = [
        {
            "type": "SOFORT-KAUFEN",
            "emoji": "🏷️",
            "min_p": MIN_PRICE,
            "url": f"https://www.ebay.de/sch/i.html?_nkw={base_query}&_sacat=9355&LH_BIN=1&_udlo={int(MIN_PRICE)}&_udhi={int(MAX_PRICE)}&_sop=10"
        },
        {
            "type": "AUKTION (< 24h Restzeit)",
            "emoji": "⏳",
            "min_p": MIN_PRICE_AUCTION,
            "url": f"https://www.ebay.de/sch/i.html?_nkw={base_query}&_sacat=9355&LH_Auction=1&LH_Time=1&_ftrt=901&_ftrv=24&_udlo={int(MIN_PRICE_AUCTION)}&_udhi={int(MAX_PRICE)}&_sop=1"
        }
    ]

    errors = []
    # eBay lehnt die erste Anfrage ohne Cookies oft mit 403 ab: Startseite zuerst laden, bei 403 einmal wiederholen
    try:
        fetch("https://www.ebay.de/")
    except Exception as e:
        print(f"eBay-Startseite: {e}")
    for mode in search_modes:
        try:
            r = fetch(mode["url"])
            if r.status_code == 403:
                time.sleep(3)
                r = fetch(mode["url"])
            if r.status_code != 200:
                errors.append(f"{mode['type']}: HTTP {r.status_code}")
                continue

            soup = BeautifulSoup(r.text, "html.parser")
            if soup.select_one("ul.srp-results, .srp-river-results") is None:
                errors.append(f"{mode['type']}: Ergebnisliste nicht gefunden")
                continue

            items = soup.select("li.s-card") or soup.select("li.s-item")
            print(f"  eBay {mode['type']}: {len(items)} Einträge")

            for item in items:
                title_elem = _first(item, [".s-card__title", ".s-item__title"])
                price_elem = _first(item, [".s-card__price", ".s-item__price"])
                link_elem = item.select_one("a[href*='/itm/']")

                if not (title_elem and price_elem and link_elem):
                    continue

                title = title_elem.get_text(" ", strip=True)
                title = re.sub(r"^Neues Angebot\s*", "", title, flags=re.IGNORECASE)
                title = re.sub(r"\s*Wird in neuem Fenster oder Tab geöffnet.*$", "", title, flags=re.IGNORECASE)
                if "Shop on eBay" in title or not title_ok(title):
                    continue

                price = parse_price(price_elem.get_text(strip=True))
                if not price or not (mode["min_p"] <= price <= MAX_PRICE):
                    continue

                id_match = re.search(r"/itm/(?:[^/?]+/)?(\d+)", link_elem["href"])
                if not id_match:
                    continue
                item_id = id_match.group(1)
                clean_link = f"https://www.ebay.de/itm/{item_id}"

                time_elem = _first(item, [".s-item__time-left", ".s-card__time-left", "[class*='time-left']"])
                if time_elem:
                    time_left = time_elem.get_text(" ", strip=True)
                else:
                    m = re.search(r"(?:noch|endet in)\s[^·|\n]{1,30}", item.get_text(" ", strip=True), flags=re.IGNORECASE)
                    time_left = m.group(0) if m else ""

                if mode["type"].startswith("AUKTION"):
                    t_low = time_left.lower()
                    if "tag" in t_low or re.search(r"\b\d+\s*t\b", t_low):
                        continue
                display_time = time_left or ("Sofort verfügbar" if mode["type"] == "SOFORT-KAUFEN" else "unbekannt")

                msg = (
                    f"{mode['emoji']} <b>EBAY {esc(mode['type'])}</b>\n"
                    f"Titel: {esc(title)}\n"
                    f"Aktueller Preis: <b>{price:.2f} €</b>\n"
                    f"Restlaufzeit: <b>{esc(display_time)}</b>\n"
                    f"👉 <a href='{esc(clean_link)}'>Zum eBay-Angebot</a>"
                )
                alert_once(state, f"ebay_{mode['type'][:3]}_{item_id}", msg)
        except Exception as e:
            errors.append(f"{mode['type']}: {e}")

    record_health(state, "eBay", not errors, "; ".join(errors))


if __name__ == "__main__":
    state = load_state()
    try:
        # Apple-Check über GitHub Actions (Alternative: local/apple_check.py – nie beide parallel betreiben)
        # eBay läuft lokal (local/ebay_check.py): eBay blockt GitHub-IPs mit HTTP 403
        for check in (check_apple_store_pickup, send_daily_restock_digest,
                      check_geizhals_shops, check_billiger, check_kleinanzeigen):
            try:
                check(state)
            except Exception as e:
                record_health(state, check.__name__, False, repr(e))
    finally:
        save_state(state)
