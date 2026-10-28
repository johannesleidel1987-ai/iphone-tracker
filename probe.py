"""
Einmaliger Diagnose-Lauf: Welche Preisvergleichsseiten liefern von GitHub Actions aus verwertbare Daten?
Gibt je URL Statuscode, Seitentitel, Größe und gefundene Produktlinks zum iPhone Duo aus.
"""
import re

import requests
from bs4 import BeautifulSoup

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "de-DE,de;q=0.9",
}

Q = "iphone duo 256gb sternenweiß"
QP = requests.utils.quote(Q)

PROBES = {
    "idealo Suche": f"https://www.idealo.de/preisvergleich/MainSearchProductCategory.html?q={QP}",
    "idealo Suche MPN": "https://www.idealo.de/preisvergleich/MainSearchProductCategory.html?q=MK2D4ZD%2FA",
    "billiger.de Suche": f"https://www.billiger.de/search?searchstring={QP}",
    "guenstiger.de Suche": f"https://www.guenstiger.de/Suche.html?q={QP}",
    "guenstiger.de Suche 2": f"https://www.guenstiger.de/Katalog/Suche/?q={QP}",
}

LINK_HINTS = ["offersofproduct", "/products/", "/produkt", "/preisvergleich/", "duo"]


def probe(name, url):
    print(f"\n=== {name} ===\n{url}")
    try:
        r = requests.get(url, headers=HEADERS, timeout=20, allow_redirects=True)
    except Exception as e:
        print(f"FEHLER: {e!r}")
        return
    print(f"HTTP {r.status_code} | {len(r.text)} Zeichen | final: {r.url}")
    soup = BeautifulSoup(r.text, "html.parser")
    title = soup.title.get_text(strip=True) if soup.title else "-"
    print(f"Titel: {title[:120]}")
    text = soup.get_text(" ", strip=True).lower()
    print(f"'duo' im Text: {text.count('duo')}x | Euro-Preise: {len(re.findall(r'\d[\d.]*,\d{2}\s*€', text))}")
    blocked = [w for w in ("captcha", "access denied", "zugriff verweigert", "robot", "cloudflare") if w in text]
    if blocked:
        print(f"Block-Hinweise: {blocked}")
    links = []
    for a in soup.select("a[href]"):
        href = a["href"]
        label = a.get_text(" ", strip=True)
        if "duo" in (href + label).lower() and any(h in href.lower() for h in LINK_HINTS):
            links.append(f"{label[:60]} -> {href[:150]}")
    for line in list(dict.fromkeys(links))[:8]:
        print(f"  Link: {line}")


if __name__ == "__main__":
    for name, url in PROBES.items():
        probe(name, url)
