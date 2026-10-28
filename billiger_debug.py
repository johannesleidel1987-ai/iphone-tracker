"""
Einmalige Diagnose: Wie stellt billiger.de die Lieferzeit je Angebot dar?
Aufruf im Ordner mit diesem Skript:  py billiger_debug.py
Speichert zusätzlich die komplette Seite als billiger_page.html.
"""
import json
import re

import requests
from bs4 import BeautifulSoup

URL = "https://www.billiger.de/products/5941011354-apple-iphone-duo-256-gb-sternenweiss"
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36",
    "Accept-Language": "de-DE,de;q=0.9",
}
DELIVERY_WORDS = re.compile(r"liefer|lager|versand|werktag|verfügbar|vorbestell|sofort|ausverkauft", re.I)

r = requests.get(URL, headers=HEADERS, timeout=20)
print(f"HTTP {r.status_code} | {len(r.text)} Zeichen")
with open("billiger_page.html", "w", encoding="utf-8") as f:
    f.write(r.text)
soup = BeautifulSoup(r.text, "html.parser")

sellers = []
for s in soup.select('script[type="application/ld+json"]'):
    try:
        d = json.loads(s.get_text())
    except ValueError:
        continue
    if isinstance(d, dict) and isinstance(d.get("offers"), dict):
        sellers = [o.get("seller", {}).get("name", "") for o in d["offers"].get("offers", [])]
print(f"Shops laut JSON-LD: {sellers}\n")


def row_for(seller):
    """Kleinstes Element, das den Shopnamen (Text oder Logo-alt) und einen Euro-Preis enthält."""
    hits = [t.parent for t in soup.find_all(string=re.compile(re.escape(seller), re.I))]
    hits += soup.find_all(attrs={"alt": re.compile(re.escape(seller), re.I)})
    hits += soup.find_all(attrs={"title": re.compile(re.escape(seller), re.I)})
    for el in hits:
        node = el
        for _ in range(12):
            if node is None or node.name == "script":
                break
            if "€" in node.get_text():
                return node
            node = node.parent
    return None


for seller in sellers:
    print(f"===== {seller} =====")
    row = row_for(seller)
    if row is None:
        print("  keine Angebotszeile gefunden\n")
        continue
    attrs = {k: v for k, v in row.attrs.items() if k != "class"}
    print(f"  Tag: <{row.name}> class={' '.join(row.get('class', []))[:150]} attrs={str(attrs)[:300]}")
    print(f"  Text: {row.get_text(' | ', strip=True)[:700]}")
    for el in row.find_all(string=DELIVERY_WORDS):
        p = el.parent
        print(f"  Lieferhinweis: '{el.strip()[:120]}' in <{p.name} class={' '.join(p.get('class', []))[:100]}>")
    print()

print("===== Alle Lieferhinweise auf der Seite (max. 40) =====")
seen = set()
for el in soup.find_all(string=DELIVERY_WORDS):
    if el.parent.name in ("script", "style"):
        continue
    t = el.strip()
    if t and t not in seen and len(t) < 150:
        seen.add(t)
        print(f"  '{t}' in <{el.parent.name} class={' '.join(el.parent.get('class', []))[:100]}>")
    if len(seen) >= 40:
        break
