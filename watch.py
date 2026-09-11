#!/usr/bin/env python3
"""
Celine Watch - surveille les pages officielles Celine Dion Paris 2026/2027
et notifie via ntfy des que quelque chose bouge (revente, mise en vente,
nouvelles dates).
"""

import base64
import difflib
import hashlib
import json
import os
import re
import sys
import time
from datetime import datetime

import requests
from bs4 import BeautifulSoup

# ---------------------------------------------------------------- config

NTFY_SERVER = os.environ.get("NTFY_SERVER", "https://ntfy.sh")
NTFY_TOPIC = os.environ.get("NTFY_TOPIC", "")
INTERVAL = int(os.environ.get("INTERVAL_SECONDS", "300"))
STATE_DIR = os.environ.get("STATE_DIR", "/data")
RUN_ONCE = os.environ.get("RUN_ONCE", "false").lower() == "true"
NOTIFY_ALL_CHANGES = os.environ.get("NOTIFY_ALL_CHANGES", "false").lower() == "true"

PAGES = [
    ("Plenitude - Celine Dion 2027",
     "https://www.plenitudearena.com/evenement/celine-dion-2027-fr/"),
    ("Plenitude - Celine Dion 2026",
     "https://www.plenitudearena.com/evenement/2026-celine-dion/"),
    ("Plenitude - Mes billets / revente",
     "https://www.plenitudearena.com/mes-billets/"),
    ("Plenitude - Actualites",
     "https://www.plenitudearena.com/actualites/"),
    ("Site officiel tournee",
     "https://paris.celinedion.com/"),
    ("AEG Presents France",
     "https://www.aegpresents.fr/event/celine-dion/"),
]

# Mots-cles qui declenchent une alerte prioritaire
KEYWORDS = [
    "revente", "remise en vente", "mise en vente", "remises en vente",
    "nouvelle vente", "nouvelles dates", "dates supplementaires",
    "billets disponibles", "places disponibles", "derniers billets",
    "ouverture des ventes", "vente exceptionnelle", "fenetre de vente",
    "bourse d'echange", "echange entre fans", "fan-to-fan",
    "resale", "on sale", "tickets available", "new dates",
]

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36")

# ---------------------------------------------------------------- utils


def log(msg):
    print(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] {msg}", flush=True)


def strip_accents_lower(s):
    repl = {"é": "e", "è": "e", "ê": "e", "ë": "e", "à": "a", "â": "a",
            "î": "i", "ï": "i", "ô": "o", "ö": "o", "ù": "u", "û": "u",
            "ü": "u", "ç": "c", "’": "'"}
    s = s.lower()
    for k, v in repl.items():
        s = s.replace(k, v)
    return s


def rfc2047(s):
    """Encode un header non-ASCII (accents francais) pour ntfy."""
    if all(ord(c) < 128 for c in s):
        return s
    b = base64.b64encode(s.encode("utf-8")).decode("ascii")
    return f"=?UTF-8?B?{b}?="


def notify(title, message, priority="default", tags="ticket", click=None):
    if not NTFY_TOPIC:
        log("!! NTFY_TOPIC non defini, notification ignoree")
        return
    headers = {
        "Title": rfc2047(title),
        "Priority": priority,
        "Tags": tags,
        "Markdown": "yes",
    }
    if click:
        headers["Click"] = click
    try:
        r = requests.post(f"{NTFY_SERVER}/{NTFY_TOPIC}",
                          data=message.encode("utf-8"),
                          headers=headers, timeout=20)
        if r.status_code >= 300:
            log(f"!! ntfy a repondu {r.status_code}: {r.text[:200]}")
    except Exception as e:
        log(f"!! envoi ntfy impossible: {e}")


def extract_text(html):
    soup = BeautifulSoup(html, "html.parser")
    for tag in soup(["script", "style", "noscript", "svg", "iframe"]):
        tag.decompose()
    text = soup.get_text("\n")
    lines = [re.sub(r"\s+", " ", l).strip() for l in text.splitlines()]
    return [l for l in lines if l]


def fetch(url):
    r = requests.get(url, headers={
        "User-Agent": UA,
        "Accept-Language": "fr-FR,fr;q=0.9",
    }, timeout=30)
    r.raise_for_status()
    r.encoding = r.apparent_encoding or "utf-8"
    return r.text


def state_path(url):
    h = hashlib.sha256(url.encode()).hexdigest()[:16]
    return os.path.join(STATE_DIR, f"{h}.json")


def load_state(url):
    p = state_path(url)
    if os.path.exists(p):
        try:
            with open(p, encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return None
    return None


def save_state(url, lines):
    with open(state_path(url), "w", encoding="utf-8") as f:
        json.dump({"lines": lines, "seen": datetime.now().isoformat()}, f)


def keyword_hits(lines):
    hits = []
    for line in lines:
        norm = strip_accents_lower(line)
        for kw in KEYWORDS:
            if strip_accents_lower(kw) in norm:
                hits.append(line)
                break
    return hits

# ---------------------------------------------------------------- core


def check_page(label, url, failures):
    try:
        lines = extract_text(fetch(url))
    except Exception as e:
        failures[url] = failures.get(url, 0) + 1
        log(f"   {label}: erreur ({e.__class__.__name__})")
        if failures[url] == 6:
            notify("Celine Watch - page inaccessible",
                   f"**{label}** ne repond plus depuis ~30 min.\n\n{url}",
                   priority="low", tags="warning", click=url)
        return
    failures[url] = 0

    old = load_state(url)
    if old is None:
        save_state(url, lines)
        log(f"   {label}: reference enregistree ({len(lines)} lignes)")
        return

    if old["lines"] == lines:
        log(f"   {label}: aucun changement")
        return

    added = [l for l in difflib.unified_diff(old["lines"], lines, lineterm="", n=0)
             if l.startswith("+") and not l.startswith("+++")]
    added = [l[1:].strip() for l in added]
    added = [l for l in added if len(l) > 3]

    hot = keyword_hits(added)
    save_state(url, lines)

    if hot:
        body = "\n".join(f"- {l[:220]}" for l in hot[:6])
        log(f"   {label}: ALERTE ({len(hot)} ligne(s) cle)")
        notify(f"🎟️ {label}",
               f"**Du nouveau sur la billetterie :**\n\n{body}\n\n{url}",
               priority="urgent", tags="rotating_light,ticket", click=url)
    elif added and NOTIFY_ALL_CHANGES:
        body = "\n".join(f"- {l[:180]}" for l in added[:5])
        log(f"   {label}: changement mineur ({len(added)} ligne(s))")
        notify(f"Celine Watch - {label}",
               f"La page a change :\n\n{body}\n\n{url}",
               priority="low", tags="eyes", click=url)
    else:
        log(f"   {label}: changement mineur ignore ({len(added)} ligne(s))")


def main():
    os.makedirs(STATE_DIR, exist_ok=True)
    if not NTFY_TOPIC:
        log("ERREUR: la variable NTFY_TOPIC est vide. Arret.")
        sys.exit(1)

    first_run = not any(os.path.exists(state_path(u)) for _, u in PAGES)
    if RUN_ONCE:
        log(f"Passage unique - {len(PAGES)} pages")
    else:
        log(f"Demarrage - {len(PAGES)} pages, verification toutes les {INTERVAL}s")
    if first_run:
        notify("Celine Watch est en ligne ✅",
               f"Surveillance de {len(PAGES)} pages officielles "
               f"(Plenitude Arena, site officiel, AEG Presents).\n"
               + ("Verification planifiee via GitHub Actions."
                  if RUN_ONCE else
                  f"Verification toutes les {INTERVAL // 60} min."),
               priority="default", tags="white_check_mark")

    failures = {}
    while True:
        log("--- tour de verification")
        for label, url in PAGES:
            check_page(label, url, failures)
            time.sleep(2)
        if RUN_ONCE:
            log("--- termine")
            return
        time.sleep(INTERVAL)


if __name__ == "__main__":
    main()
