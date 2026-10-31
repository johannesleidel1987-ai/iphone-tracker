import html
import json
import os
import re
import time
from datetime import datetime
from urllib.parse import quote_plus
from zoneinfo import ZoneInfo

import requests
from bs4 import BeautifulSoup

# ================= KONFIGURATION =================
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "")

MIN_PRICE_AUCTION = 500.00  # Niedrigerer Startfilter für laufende Auktionen (< 24h)

# Überwachte Varianten (alle Sternenweiß). Je Variante eigener Preisrahmen und eigene Quellen-URLs.
# min_price schützt vor Hüllen, Dummys und Fake-Inseraten bei Sofort-Kaufen.
# mpn: nur deutsche Teilenummern – ausländische Suffixe (QL/A, SX/A) kennt apple.com/de nicht.
VARIANTS = [
    {
        "storage": "256",
        "label": "iPhone Duo 256 GB Sternenweiß",
        "mpn": "MK2D4ZD/A",
        "min_price": 1850.00,
        "max_price": 2400.00,   # UVP 2.299 €
    },
    {
        "storage": "512",
        "label": "iPhone Duo 512 GB Sternenweiß",
        "mpn": "MK2F4ZD/A",
        "min_price": 2100.00,
        "max_price": 2650.00,   # UVP 2.549 €
    },
]

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
MORNING_TIME = (7, 30)                  # erster Lauf ab 07:30 sendet die Nachschub-Übersicht aller Stores
RESTOCK_LOG_RETENTION_DAYS = 14

# eBay: Titel muss "duo" und die Speichergröße der Variante enthalten.

STATE_FILE = "state.json"
LEGACY_SEEN_FILE = "seen_offers.json"   # wird beim ersten Lauf einmalig übernommen
SEEN_RETENTION_DAYS = 30

HEALTH_FAIL_THRESHOLD = 15              # Fehlläufe in Folge bis zur Warnung (= 30 Min bei 2-Min-Takt)
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
    state.setdefault("seen", {})     # offer_id -> Zeitstempel (eBay)
    state.setdefault("active", {})   # Kanal -> aktuell verfügbare Treffer (Apple)
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


def title_ok(title, variant):
    t = title.lower()
    return "duo" in t and variant["storage"] in t and not any(bad in t for bad in BLACKLIST)


# ---------------- Apple ----------------

TARGET_MPNS = [v["mpn"] for v in VARIANTS]
VARIANT_BY_MPN = {v["mpn"]: v for v in VARIANTS}
STORAGE_LIST = " / ".join(f"{v['storage']} GB" for v in VARIANTS)
APPLE_BUY_URL = "https://www.apple.com/de/shop/buy-iphone/iphone-duo"


def apple_parts(state):
    """Alle deutschen Teilenummern des iPhone Duo (Farben x Speicher), einmal täglich von der Kaufseite gelesen."""
    cache = state.setdefault("apple_parts", {"date": "", "parts": []})
    today = datetime.now(TZ).date().isoformat()
    if cache["date"] != today or not cache["parts"]:
        try:
            r = fetch(APPLE_BUY_URL)
            found = sorted(set(re.findall(r"\bM[A-Z0-9]{4}ZD/A\b", r.text))) if r.status_code == 200 else []
            if found:
                cache.update(date=today, parts=found[:15])
                print(f"  Apple-Teilenummern: {', '.join(cache['parts'])}")
        except Exception as e:
            print(f"  Apple-Kaufseite: {e}")
    parts = [p for p in cache["parts"] if p not in TARGET_MPNS]
    return TARGET_MPNS + parts


def check_apple_store_pickup(state):
    """Prüft die Apple-Abholverfügbarkeit aller iPhone-Duo-Varianten.
    Sofort-Alarm nur für TARGET_MPNS; alle Varianten fließen ins Restock-Protokoll (Morgenmeldung)."""
    parts = apple_parts(state)
    parts_query = "&".join([f"parts.{i}={requests.utils.quote(mpn)}" for i, mpn in enumerate(parts)])
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
                state.setdefault("apple_stores", {})[store_number or store_name] = {"store": store_name, "city": city}

                parts_avail = store.get("partsAvailability", {})
                for mpn in parts:
                    part_info = parts_avail.get(mpn)
                    if part_info is None and mpn not in TARGET_MPNS:
                        continue  # Teilenummer an diesem Store unbekannt
                    part_info = part_info or {}
                    title = (part_info.get("messageTypes", {}).get("regular", {})
                             .get("storePickupProductTitle", "") or mpn)
                    if mpn not in TARGET_MPNS and not re.search(r"iphone duo\b.*\d+\s*(gb|tb)", title.lower()):
                        continue  # Zubehör o. Ä. von der Kaufseite
                    key = f"{store_number or store_name}|{mpn}"
                    if key in observed:
                        continue  # Store bereits über andere PLZ erfasst
                    observed[key] = {
                        "available": part_info.get("pickupDisplay") == "available",
                        "store": store_name,
                        "store_id": store_number or store_name,
                        "city": city,
                        "mpn": mpn,
                        "title": title.replace("iPhone Duo", "").strip() or mpn,
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
        if info["mpn"] not in TARGET_MPNS:
            continue
        if not info["available"]:
            active.pop(key, None)
            continue
        if active.get(key):
            continue
        msg = (
            f"🍏 <b>APPLE STORE PICKUP VERFÜGBAR!</b>\n"
            f"Store: <b>Apple {esc(info['store'])} ({esc(info['city'])})</b>\n"
            f"Modell: <b>{esc(VARIANT_BY_MPN[info['mpn']]['label'])}</b> ({esc(info['mpn'])})\n"
            f"Status: <b>{esc(info['quote'])}</b>\n"
            f"👉 <a href='{APPLE_BUY_URL}'>Jetzt direkt reservieren</a>"
        )
        if send_telegram(msg):
            active[key] = True


def log_restock_events(state, observed):
    """Protokolliert Wechsel verfügbar/nicht verfügbar je Store für die Tagesmeldung."""
    status = state["apple_status"]
    log = state["restock_log"]
    now = time.time()
    for key, info in observed.items():
        if key not in status and info["mpn"] not in TARGET_MPNS:
            status[key] = info["available"]  # neue Variante: erst Ausgangslage merken, kein Restock
            continue
        was = status.get(key, False)
        if info["available"] != was:
            log.append({
                "ts": now, "key": key, "store": info["store"], "store_id": info["store_id"],
                "city": info["city"], "mpn": info["mpn"], "title": info["title"],
                "event": "in" if info["available"] else "out",
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
    target_log = [e for e in state["restock_log"] if e.get("mpn", TARGET_MPNS[0]) in TARGET_MPNS]

    def digest_label(e):
        storage = VARIANT_BY_MPN[e.get("mpn", TARGET_MPNS[0])]["storage"]
        return f"Apple {e['store']} ({e['city']}) – {storage} GB"

    by_store = {}
    for e in sorted(target_log, key=lambda e: e["ts"]):
        if e["ts"] < day_start:
            continue
        label = digest_label(e)
        windows = by_store.setdefault(label, [])
        if e["event"] == "in":
            windows.append([e["ts"], None])
        elif windows and windows[-1][1] is None:
            windows[-1][1] = e["ts"]
        else:
            windows.append([None, e["ts"]])  # war schon vor Mitternacht verfügbar

    # Stores, die den ganzen Tag ohne Wechsel verfügbar waren
    for e in reversed(target_log):
        label = digest_label(e)
        if state["apple_status"].get(e["key"]) and e["event"] == "in" and label not in by_store:
            by_store[label] = [[None, None]]

    lines = [f"📊 <b>Apple Restocks heute ({now_local.strftime('%d.%m.%Y')})</b>",
             f"iPhone Duo Sternenweiß {STORAGE_LIST}"]
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
    lines.append("\n<i>Zeiten auf ±2 Min genau (Prüftakt).</i>")

    if send_telegram("\n".join(lines)):
        state["last_digest"] = today


def _when(ts, now_local):
    d = datetime.fromtimestamp(ts, TZ)
    delta = (now_local.date() - d.date()).days
    day = {0: "heute", 1: "gestern"}.get(delta, d.strftime("%a %d.%m.").replace(
        "Mon", "Mo").replace("Tue", "Di").replace("Wed", "Mi").replace("Thu", "Do")
        .replace("Fri", "Fr").replace("Sat", "Sa").replace("Sun", "So"))
    return f"{day} {d.strftime('%H:%M')}"


def send_morning_report(state):
    """Einmal täglich ab MORNING_TIME: je Store letzter Nachschub (alle Varianten), übliche Uhrzeit, Anzahl diese Woche."""
    now_local = datetime.now(TZ)
    today = now_local.date().isoformat()
    if (now_local.hour, now_local.minute) < MORNING_TIME or state.get("last_morning") == today:
        return

    week_start = datetime(now_local.year, now_local.month, now_local.day, tzinfo=TZ).timestamp() \
        - now_local.weekday() * 86400
    seven_days = time.time() - 7 * 86400
    ins = [e for e in state["restock_log"] if e["event"] == "in" and e.get("store_id")]

    lines = [f"🌅 <b>Apple-Nachschub – Stand {now_local.strftime('%d.%m. %H:%M')}</b>",
             "iPhone Duo, alle Farben und Speichergrößen"]
    stores = state.get("apple_stores", {})
    if not stores:
        lines.append("Noch keine Store-Daten.")
    for sid, meta in sorted(stores.items(), key=lambda x: x[1]["city"]):
        mine = [e for e in ins if e["store_id"] == sid]
        lines.append(f"\n<b>{esc(meta['store'])} ({esc(meta['city'])})</b>")
        if not mine:
            lines.append("• noch kein Nachschub erfasst")
            continue
        last_ts = max(e["ts"] for e in mine)
        last_items = sorted({e.get("title", e["mpn"]) for e in mine if e["ts"] == last_ts})
        target_hit = any(e["mpn"] in TARGET_MPNS for e in mine if e["ts"] == last_ts)
        lines.append(f"• zuletzt aufgefüllt <b>{_when(last_ts, now_local)}</b>: {esc(', '.join(last_items))}"
                     + (" ⭐" if target_hit else ""))
        runs_week = {e["ts"] for e in mine if e["ts"] >= week_start}
        hours = [datetime.fromtimestamp(t, TZ).hour for t in {e["ts"] for e in mine if e["ts"] >= seven_days}]
        usual = f"meist gegen {max(set(hours), key=hours.count):02d}:00 · " if len(hours) >= 2 else ""
        lines.append(f"• {usual}diese Woche {len(runs_week)}-mal aufgefüllt")
    n_variants = len({k.split("|", 1)[1] for k in state["apple_status"]})
    lines.append(f"\n⭐ = Sternenweiß {STORAGE_LIST} dabei · {n_variants} Varianten erfasst · <i>Zeiten ±2 Min.</i>")

    if send_telegram("\n".join(lines)):
        state["last_morning"] = today


# ---------------- eBay ----------------

def _first(item, selectors):
    for sel in selectors:
        elem = item.select_one(sel)
        if elem:
            return elem
    return None


def ebay_search_modes(variant):
    """Sofort-Kaufen und Auktionen (< 24h) für eine Variante."""
    query = quote_plus(f"iphone duo {variant['storage']}gb (sternenweiß, weiß, star white, {variant['mpn']}) "
                       f"-hülle -case -folie")
    max_p = int(variant["max_price"])
    base = f"https://www.ebay.de/sch/i.html?_nkw={query}&_sacat=9355"
    return [
        {
            "type": "SOFORT-KAUFEN",
            "emoji": "🏷️",
            "min_p": variant["min_price"],
            "url": f"{base}&LH_BIN=1&_udlo={int(variant['min_price'])}&_udhi={max_p}&_sop=10"
        },
        {
            "type": "AUKTION (< 24h Restzeit)",
            "emoji": "⏳",
            "min_p": MIN_PRICE_AUCTION,
            "url": f"{base}&LH_Auction=1&LH_Time=1&_ftrt=901&_ftrv=24&_udlo={int(MIN_PRICE_AUCTION)}&_udhi={max_p}&_sop=1"
        }
    ]


def check_ebay(state):
    """
    Prüft eBay.de je Variante getrennt nach:
    1. Sofort-Kaufen (LH_BIN=1) im Preisrahmen der Variante
    2. Auktionen (LH_Auction=1) mit Restlaufzeit < 24 Stunden und aktuellem Preis <= max_price
    Unterstützt das neue Karten-Layout (.s-card) und das alte Layout (.s-item).
    """
    errors = []
    # eBay lehnt die erste Anfrage ohne Cookies oft mit 403 ab: Startseite zuerst laden, bei 403 einmal wiederholen
    try:
        fetch("https://www.ebay.de/")
    except Exception as e:
        print(f"eBay-Startseite: {e}")
    for variant, mode in [(v, m) for v in VARIANTS for m in ebay_search_modes(v)]:
        mode_name = f"{variant['storage']} GB {mode['type']}"
        try:
            r = fetch(mode["url"])
            if r.status_code == 403:
                time.sleep(3)
                r = fetch(mode["url"])
            if r.status_code != 200:
                errors.append(f"{mode_name}: HTTP {r.status_code}")
                continue

            soup = BeautifulSoup(r.text, "html.parser")
            if soup.select_one("ul.srp-results, .srp-river-results") is None:
                errors.append(f"{mode_name}: Ergebnisliste nicht gefunden")
                continue

            items = soup.select("li.s-card") or soup.select("li.s-item")
            print(f"  eBay {mode_name}: {len(items)} Einträge")

            for item in items:
                title_elem = _first(item, [".s-card__title", ".s-item__title"])
                price_elem = _first(item, [".s-card__price", ".s-item__price"])
                link_elem = item.select_one("a[href*='/itm/']")

                if not (title_elem and price_elem and link_elem):
                    continue

                title = title_elem.get_text(" ", strip=True)
                title = re.sub(r"^Neues Angebot\s*", "", title, flags=re.IGNORECASE)
                title = re.sub(r"\s*Wird in neuem Fenster oder Tab geöffnet.*$", "", title, flags=re.IGNORECASE)
                if "Shop on eBay" in title or not title_ok(title, variant):
                    continue

                price = parse_price(price_elem.get_text(strip=True))
                if not price or not (mode["min_p"] <= price <= variant["max_price"]):
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
                    f"{mode['emoji']} <b>EBAY {esc(mode['type'])} – {variant['storage']} GB</b>\n"
                    f"Titel: {esc(title)}\n"
                    f"Aktueller Preis: <b>{price:.2f} €</b>\n"
                    f"Restlaufzeit: <b>{esc(display_time)}</b>\n"
                    f"👉 <a href='{esc(clean_link)}'>Zum eBay-Angebot</a>"
                )
                alert_once(state, f"ebay_{mode['type'][:3]}_{item_id}", msg)
        except Exception as e:
            errors.append(f"{mode_name}: {e}")

    record_health(state, "eBay", not errors, "; ".join(errors))


if __name__ == "__main__":
    state = load_state()
    try:
        # Apple-Check über GitHub Actions (Alternative: local/apple_check.py – nie beide parallel betreiben)
        # eBay läuft lokal (local/ebay_check.py): eBay blockt GitHub-IPs mit HTTP 403
        for check in (check_apple_store_pickup, send_morning_report, send_daily_restock_digest):
            try:
                check(state)
            except Exception as e:
                record_health(state, check.__name__, False, repr(e))
    finally:
        save_state(state)
