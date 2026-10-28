"""
Apple-Store-Bestandsprüfung für iPhone Duo 256 GB Sternenweiß – lokal unter Windows.

Läuft per Aufgabenplanung alle 5 Minuten vom Heimnetz aus (GitHub-/Azure-IPs blockt Apple mit HTTP 541).
Nur Python-Standardbibliothek, keine pip-Installation nötig.

Aufruf:
  python apple_check.py          normaler Lauf (Aufgabenplanung)
  python apple_check.py --test   Stores anzeigen und Telegram-Testnachricht senden
"""
import html
import json
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

BASE = Path(__file__).resolve().parent
CONFIG_FILE = BASE / "config.json"
STATE_FILE = BASE / "apple_state.json"
LOG_FILE = BASE / "apple_check.log"

MPN = "MK2D4ZD/A"  # iPhone Duo 256 GB Sternenweiß (DE)
SEARCH_PLZ = ["80331", "86150", "71065"]  # München, Augsburg, Sindelfingen
ALLOWED = ["rosenstraße", "rosenstrasse", "oez", "olympia", "city-galerie", "augsburg",
           "sindelfingen", "breuningerland", "münchen", "muenchen", "munich"]
EXPECTED_CITIES = ["münchen", "augsburg", "sindelfingen"]

DIGEST_HOUR = 21                 # Tagesmeldung beim ersten Lauf ab 21:00 Ortszeit
LOG_RETENTION_DAYS = 14
HEALTH_FAIL_THRESHOLD = 6        # 30 Min bei 5-Min-Takt
HEALTH_WARN_INTERVAL = 24 * 3600
BUY_URL = "https://www.apple.com/de/shop/buy-iphone/iphone-duo"

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36",
    "Accept": "application/json",
    "Accept-Language": "de-DE,de;q=0.9",
}


def log(msg):
    line = f"{time.strftime('%Y-%m-%d %H:%M:%S')} {msg}"
    print(line)
    try:
        lines = LOG_FILE.read_text(encoding="utf-8").splitlines()[-1000:] if LOG_FILE.exists() else []
        lines.append(line)
        LOG_FILE.write_text("\n".join(lines) + "\n", encoding="utf-8")
    except OSError:
        pass


def esc(v):
    return html.escape(str(v), quote=True)


def load_json(path, default):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return default


CONFIG = load_json(CONFIG_FILE, {})


def send_telegram(text):
    token, chat = CONFIG.get("telegram_bot_token"), CONFIG.get("telegram_chat_id")
    if not (token and chat):
        log("Telegram nicht konfiguriert (config.json). Nachricht:\n" + text)
        return False
    data = json.dumps({"chat_id": chat, "text": text, "parse_mode": "HTML"}).encode()
    req = urllib.request.Request(f"https://api.telegram.org/bot{token}/sendMessage", data=data,
                                 headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=15) as r:
            return json.load(r).get("ok", False)
    except Exception as e:
        log(f"Telegram-Fehler: {e}")
        return False


# ---------------- Apple ----------------

def query_stores():
    """Gibt (observed, errors) zurück. observed: Store-Nummer -> Info."""
    observed, errors, cities = {}, [], set()
    for plz in SEARCH_PLZ:
        qs = urllib.parse.urlencode({"pl": "true", "mts.0": "regular", "parts.0": MPN, "location": plz})
        url = f"https://www.apple.com/de/shop/fulfillment-messages?{qs}"
        try:
            with urllib.request.urlopen(urllib.request.Request(url, headers=HEADERS), timeout=15) as r:
                body = json.load(r)
        except urllib.error.HTTPError as e:
            errors.append(f"PLZ {plz}: HTTP {e.code}")
            continue
        except Exception as e:
            errors.append(f"PLZ {plz}: {e}")
            continue
        stores = body.get("body", {}).get("content", {}).get("pickupMessage", {}).get("stores", [])
        if not stores:
            errors.append(f"PLZ {plz}: keine Stores in der Antwort")
            continue
        for s in stores:
            name, city, num = s.get("storeName", ""), s.get("city", ""), s.get("storeNumber", "")
            if not any(a in f"{name} {city}".lower() for a in ALLOWED):
                continue
            cities.add(city.lower())
            key = num or name
            if key in observed:
                continue
            part = s.get("partsAvailability", {}).get(MPN, {})
            observed[key] = {
                "store": name, "city": city,
                "available": part.get("pickupDisplay") == "available",
                "quote": part.get("pickupSearchQuote", ""),
            }
    missing = [c for c in EXPECTED_CITIES if not any(c in x for x in cities)]
    if missing and not errors:
        errors.append(f"Erwartete Stores fehlen: {', '.join(missing)}")
    return observed, errors


def record_health(state, ok, detail):
    h = state.setdefault("health", {"fails": 0, "last_warn": 0})
    if ok:
        if h["fails"] >= HEALTH_FAIL_THRESHOLD and h["last_warn"]:
            send_telegram("✅ <b>Apple-Check (lokal) funktioniert wieder</b>")
        h.update(fails=0, last_warn=0)
        return
    h["fails"] += 1
    log(f"Fehlschlag #{h['fails']}: {detail}")
    if h["fails"] >= HEALTH_FAIL_THRESHOLD and time.time() - h["last_warn"] >= HEALTH_WARN_INTERVAL:
        if send_telegram(f"⚠️ <b>Apple-Check (lokal): {h['fails']} Fehlläufe in Folge</b>\n"
                         f"Letzter Fehler: {esc(detail)}"):
            h["last_warn"] = time.time()


def process(state, observed):
    status = state.setdefault("status", {})     # Store -> zuletzt verfügbar
    alerted = state.setdefault("alerted", {})   # Store -> Alarm für aktuelle Verfügbarkeit gesendet
    events = state.setdefault("events", [])
    now = time.time()
    for key, info in observed.items():
        if info["available"] != status.get(key, False):
            events.append({"ts": now, "key": key, "store": info["store"], "city": info["city"],
                           "event": "in" if info["available"] else "out"})
            log(f"{info['store']}: {'VERFÜGBAR' if info['available'] else 'ausverkauft'}")
        status[key] = info["available"]

        if not info["available"]:
            alerted.pop(key, None)
        elif not alerted.get(key):
            msg = (f"🍏 <b>APPLE STORE PICKUP VERFÜGBAR!</b>\n"
                   f"Store: <b>Apple {esc(info['store'])} ({esc(info['city'])})</b>\n"
                   f"Modell: iPhone Duo 256 GB Sternenweiß ({MPN})\n"
                   f"Status: <b>{esc(info['quote'] or 'Verfügbar')}</b>\n"
                   f"👉 <a href='{BUY_URL}'>Jetzt reservieren</a>")
            if send_telegram(msg):
                alerted[key] = True
    cutoff = now - LOG_RETENTION_DAYS * 86400
    state["events"] = [e for e in events if e["ts"] >= cutoff]


def hm(ts):
    return time.strftime("%H:%M", time.localtime(ts))


def daily_digest(state):
    lt = time.localtime()
    today = time.strftime("%Y-%m-%d", lt)
    if lt.tm_hour < DIGEST_HOUR or state.get("last_digest") == today:
        return
    day_start = time.mktime((lt.tm_year, lt.tm_mon, lt.tm_mday, 0, 0, 0, 0, 0, -1))

    by_store = {}
    for e in sorted(state.get("events", []), key=lambda e: e["ts"]):
        if e["ts"] < day_start:
            continue
        w = by_store.setdefault(f"Apple {e['store']} ({e['city']})", [])
        if e["event"] == "in":
            w.append([e["ts"], None])
        elif w and w[-1][1] is None:
            w[-1][1] = e["ts"]
        else:
            w.append([None, e["ts"]])
    for e in reversed(state.get("events", [])):
        label = f"Apple {e['store']} ({e['city']})"
        if state["status"].get(e["key"]) and e["event"] == "in" and label not in by_store:
            by_store[label] = [[None, None]]

    lines = [f"📊 <b>Apple Restocks heute ({time.strftime('%d.%m.%Y', lt)})</b>",
             "iPhone Duo 256 GB Sternenweiß"]
    if not by_store:
        lines.append("Heute kein Bestand in München, Augsburg oder Sindelfingen.")
    for label, windows in by_store.items():
        lines.append(f"\n<b>{esc(label)}</b>")
        for start, end in windows:
            if start and end:
                lines.append(f"• {hm(start)}–{hm(end)} Uhr ({round((end - start) / 60)} Min)")
            elif start:
                lines.append(f"• ab {hm(start)} Uhr, noch verfügbar")
            elif end:
                lines.append(f"• seit Vortag verfügbar, bis {hm(end)} Uhr")
            else:
                lines.append("• ganztägig verfügbar")
    fails = state.get("health", {}).get("fails", 0)
    if fails:
        lines.append(f"\n⚠️ Aktuell {fails} Fehlläufe in Folge – siehe apple_check.log")
    lines.append("\n<i>Zeiten auf ±5 Min genau (Prüftakt).</i>")
    if send_telegram("\n".join(lines)):
        state["last_digest"] = today


def main():
    test = "--test" in sys.argv
    state = load_json(STATE_FILE, {})
    observed, errors = query_stores()
    record_health(state, not errors, "; ".join(errors))
    if test:
        for info in observed.values():
            print(f"  {info['store']} ({info['city']}): {'verfügbar' if info['available'] else 'nicht verfügbar'}")
        print("Fehler: " + ("; ".join(errors) or "keine"))
        ok = send_telegram("🧪 <b>Apple-Check (lokal) Testnachricht</b>\n"
                           f"{len(observed)} Stores gefunden, Fehler: {esc('; '.join(errors) or 'keine')}")
        print("Telegram: " + ("gesendet" if ok else "FEHLGESCHLAGEN"))
    process(state, observed)
    if not test:
        daily_digest(state)
    STATE_FILE.write_text(json.dumps(state, ensure_ascii=False), encoding="utf-8")


if __name__ == "__main__":
    main()
