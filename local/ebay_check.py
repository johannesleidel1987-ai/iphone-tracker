"""
eBay-Check lokal unter Windows: eBay blockt GitHub-Actions-IPs mit HTTP 403.
Nutzt check_ebay() aus ../tracker.py, damit Filter und Meldungen identisch bleiben.

Voraussetzung (einmalig):  py -m pip install requests beautifulsoup4
Aufruf:  py ebay_check.py
"""
import json
import os
import sys
from pathlib import Path

BASE = Path(__file__).resolve().parent
config = json.loads((BASE / "config.json").read_text(encoding="utf-8"))
os.environ["TELEGRAM_BOT_TOKEN"] = config["telegram_bot_token"]
os.environ["TELEGRAM_CHAT_ID"] = str(config["telegram_chat_id"])

sys.path.insert(0, str(BASE.parent))
import tracker  # noqa: E402

tracker.STATE_FILE = str(BASE / "ebay_state.json")
tracker.LEGACY_SEEN_FILE = str(BASE / "_kein_altbestand.json")

if __name__ == "__main__":
    state = tracker.load_state()
    try:
        tracker.check_ebay(state)
    except Exception as e:
        tracker.record_health(state, "eBay (lokal)", False, repr(e))
    finally:
        tracker.save_state(state)
