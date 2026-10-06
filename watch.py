#!/usr/bin/env python3
"""
Celine Watch - surveille les pages officielles Celine Dion Paris 2026/2027
et notifie via ntfy des que quelque chose bouge (revente, mise en vente,
nouvelles dates).
"""

import base64
import concurrent.futures
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
    ("Ticketmaster - fiche evenement",
     "https://help.ticketmaster.fr/hc/en-us/articles/45193915141905-C%C3%89LINE-DION-PARIS-2026-2027"),
    ("Plenitude - annonce revente officielle",
     "https://www.plenitudearena.com/en/official-resale-of-celine-dion-tickets-for-paris-2026-the-service-is-now-open-at-plenitude-arena/"),
    ("AXS - serie Paris 2026",
     "https://www.axs.com/fr/series/33807/celine-dion-paris-2026-tickets?skin=celine"),
    ("AXS - serie Paris 2027",
     "https://www.axs.com/fr/series/33315/celine-dion-paris-2027-tickets?skin=celine"),
]

# Pages AXS par date de concert : (date ISO, URL). Chaque page liste les
# offres ouvertes pour CE soir-la (Standard, Premium, revente...).
AXS_PAR_DATE = [
    ("2026-09-16", "https://www.axs.com/fr/events/1381037"),
    ("2026-09-18", "https://www.axs.com/fr/events/1381048"),
    ("2026-09-19", "https://www.axs.com/fr/events/1381038"),
    ("2026-09-23", "https://www.axs.com/fr/events/1381039"),
    ("2026-09-25", "https://www.axs.com/fr/events/1381050"),
    ("2026-09-26", "https://www.axs.com/fr/events/1381040"),
    ("2026-09-30", "https://www.axs.com/fr/events/1381041"),
    ("2026-10-02", "https://www.axs.com/fr/events/1381052"),
    ("2026-10-03", "https://www.axs.com/fr/events/1381042"),
    ("2026-10-07", "https://www.axs.com/fr/events/1381044"),
    ("2026-10-09", "https://www.axs.com/fr/events/1381053"),
    ("2026-10-10", "https://www.axs.com/fr/events/1381045"),
    ("2026-10-14", "https://www.axs.com/fr/events/1381046"),
    ("2026-10-16", "https://www.axs.com/fr/events/1381055"),
    ("2026-10-17", "https://www.axs.com/fr/events/1381057"),
    ("2027-05-08", "https://www.axs.com/fr/events/1444112/slug/promopage/82430"),
    ("2027-05-12", "https://www.axs.com/fr/events/1444113/slug/promopage/82431"),
    ("2027-05-14", "https://www.axs.com/fr/events/1444114/slug/promopage/82432"),
    ("2027-05-15", "https://www.axs.com/fr/events/1444115/slug/promopage/82433"),
    ("2027-05-19", "https://www.axs.com/fr/events/1444116/slug/promopage/82434"),
    ("2027-05-21", "https://www.axs.com/fr/events/1444117/slug/promopage/82435"),
    ("2027-05-22", "https://www.axs.com/fr/events/1444118/slug/promopage/82436"),
    ("2027-05-26", "https://www.axs.com/fr/events/1444119/slug/promopage/82437"),
    ("2027-05-28", "https://www.axs.com/fr/events/1444120/slug/promopage/82438"),
    ("2027-05-29", "https://www.axs.com/fr/events/1444121/slug/promopage/82439"),
]
PAGE_DATE = {url: d for d, url in AXS_PAR_DATE}

# Mots-cles qui declenchent une alerte prioritaire
KEYWORDS = [
    "revente", "remise en vente", "mise en vente", "remises en vente",
    "nouvelle vente", "nouvelles dates", "dates supplementaires",
    "billets disponibles", "places disponibles", "derniers billets",
    "ouverture des ventes", "vente exceptionnelle", "fenetre de vente",
    "bourse d'echange", "echange entre fans", "fan-to-fan",
    "remise en circulation", "places liberees", "liberation de places",
    "resale", "on sale", "tickets available", "new dates",
    "now available", "released", "additional dates", "may 2027", "mai 2027",
    "acheter des billets", "revente officielle", "marketplace",
]

# Les 26 dates de la residence (AAAA-MM-JJ)
CONCERT_DATES = {
    "2026-09-12", "2026-09-16", "2026-09-18", "2026-09-19", "2026-09-23",
    "2026-09-25", "2026-09-26", "2026-09-30", "2026-10-02", "2026-10-03",
    "2026-10-07", "2026-10-09", "2026-10-10", "2026-10-14", "2026-10-16",
    "2026-10-17",
    "2027-05-08", "2027-05-12", "2027-05-14", "2027-05-15", "2027-05-19",
    "2027-05-21", "2027-05-22", "2027-05-26", "2027-05-28", "2027-05-29",
}

# Boutons ajoutes a chaque alerte (ntfy en accepte 3 maximum)
BUTTONS = [
    ("Billetterie Plenitude", "https://www.plenitudearena.com/billetterie/"),
    ("Site officiel (AXS/TM/Fnac)", "https://paris.celinedion.com/"),
]

MONTHS = {
    "janvier": 1, "janv": 1, "january": 1, "jan": 1,
    "fevrier": 2, "fevr": 2, "fev": 2, "february": 2, "feb": 2,
    "mars": 3, "march": 3, "mar": 3,
    "avril": 4, "avr": 4, "april": 4, "apr": 4,
    "mai": 5, "may": 5,
    "juin": 6, "june": 6, "jun": 6,
    "juillet": 7, "juil": 7, "july": 7, "jul": 7,
    "aout": 8, "august": 8, "aug": 8,
    "septembre": 9, "sept": 9, "sep": 9, "september": 9,
    "octobre": 10, "oct": 10, "october": 10,
    "novembre": 11, "nov": 11, "november": 11,
    "decembre": 12, "dec": 12, "december": 12,
}
_MONTH_RE = "|".join(sorted(MONTHS, key=len, reverse=True))
DATE_FR = re.compile(rf"\b(\d{{1,2}})(?:er)?\s+({_MONTH_RE})\.?\s*(\d{{4}})?\b")
DATE_EN = re.compile(rf"\b({_MONTH_RE})\.?\s+(\d{{1,2}})(?:st|nd|rd|th)?,?\s*(\d{{4}})?\b")
JOURS = ["lun.", "mar.", "mer.", "jeu.", "ven.", "sam.", "dim."]
MOIS_COURT = ["", "janv.", "fevr.", "mars", "avr.", "mai", "juin", "juil.",
              "aout", "sept.", "oct.", "nov.", "dec."]

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


def _guess_year(month):
    if month == 5:
        return 2027
    if month in (9, 10):
        return 2026
    return None


def concert_dates_in(lines):
    """Renvoie les dates de concert (AAAA-MM-JJ) citees dans les lignes."""
    found = set()
    for line in lines:
        t = strip_accents_lower(line)
        for m in DATE_FR.finditer(t):
            day, mon, year = int(m.group(1)), MONTHS[m.group(2)], m.group(3)
            year = int(year) if year else _guess_year(mon)
            if year:
                found.add(f"{year:04d}-{mon:02d}-{day:02d}")
        for m in DATE_EN.finditer(t):
            mon, day, year = MONTHS[m.group(1)], int(m.group(2)), m.group(3)
            year = int(year) if year else _guess_year(mon)
            if year:
                found.add(f"{year:04d}-{mon:02d}-{day:02d}")
    return sorted(d for d in found if d in CONCERT_DATES)


def fmt_date(iso):
    d = datetime.strptime(iso, "%Y-%m-%d")
    return f"{JOURS[d.weekday()]} {d.day} {MOIS_COURT[d.month]} {d.year}"


def actions_header(page_url):
    acts = [("Voir la page", page_url)] + BUTTONS
    return "; ".join(f"view, {label}, {url}" for label, url in acts[:3])


def notify(title, message, priority="default", tags="ticket", click=None,
           actions=None):
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
    if actions:
        headers["Actions"] = rfc2047(actions)
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


FETCH_TIMEOUT = int(os.environ.get("FETCH_TIMEOUT", "12"))
MAX_PARALLEL = int(os.environ.get("MAX_PARALLEL", "4"))
# Les pages AXS ne sont verifiees qu'une fois toutes les N minutes, pour ne pas
# marteler leur site (protection anti-bot). Les pages d'annonces restent a
# chaque passage.
AXS_INTERVAL_MIN = int(os.environ.get("AXS_INTERVAL_MIN", "30"))


def fetch(url):
    r = requests.get(url, headers={
        "User-Agent": UA,
        "Accept-Language": "fr-FR,fr;q=0.9",
    }, timeout=FETCH_TIMEOUT)
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


def axs_a_verifier():
    """Vrai si le tour AXS est du (>= AXS_INTERVAL_MIN depuis le dernier)."""
    p = os.path.join(STATE_DIR, "_axs_last.json")
    now = time.time()
    try:
        with open(p, encoding="utf-8") as f:
            last = json.load(f).get("t", 0)
    except Exception:
        last = 0
    if now - last < AXS_INTERVAL_MIN * 60:
        return False
    with open(p, "w", encoding="utf-8") as f:
        json.dump({"t": now, "iso": datetime.now().isoformat()}, f)
    return True


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


def fetch_all(pages):
    """Recupere toutes les pages en parallele. Renvoie url -> lignes ou Exception."""
    out = {}
    with concurrent.futures.ThreadPoolExecutor(max_workers=MAX_PARALLEL) as pool:
        futures = {pool.submit(fetch, url): url for _, url in pages}
        for fut in concurrent.futures.as_completed(futures):
            url = futures[fut]
            try:
                out[url] = extract_text(fut.result())
            except Exception as e:
                out[url] = e
    return out


def check_page(label, url, failures, result):
    if isinstance(result, Exception):
        failures[url] = failures.get(url, 0) + 1
        log(f"   {label}: erreur ({result.__class__.__name__})")
        if failures[url] == 6:
            notify("Celine Watch - page inaccessible",
                   f"**{label}** ne repond plus depuis ~30 min.\n\n{url}",
                   priority="low", tags="warning", click=url)
        return
    lines = result
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
        dates = concert_dates_in(added)
        if url in PAGE_DATE and PAGE_DATE[url] not in dates:
            dates = [PAGE_DATE[url]] + dates
        if dates:
            dates_txt = ("\n\n📅 **Concerts cites :** "
                         + ", ".join(fmt_date(d) for d in dates))
        else:
            dates_txt = "\n\n📅 Aucune date de concert precise dans le texte."
        log(f"   {label}: ALERTE ({len(hot)} ligne(s) cle, {len(dates)} date(s))")
        notify(f"🎟️ {label}",
               f"**Du nouveau sur la billetterie :**\n\n{body}{dates_txt}",
               priority="urgent", tags="rotating_light,ticket", click=url,
               actions=actions_header(url))
    elif added and NOTIFY_ALL_CHANGES:
        body = "\n".join(f"- {l[:180]}" for l in added[:5])
        log(f"   {label}: changement mineur ({len(added)} ligne(s))")
        notify(f"Celine Watch - {label}",
               f"La page a change :\n\n{body}\n\n{url}",
               priority="low", tags="eyes", click=url)
    else:
        log(f"   {label}: changement mineur ignore ({len(added)} ligne(s))")


def main():
    for iso, url in AXS_PAR_DATE:
        if url not in {u for _, u in PAGES}:
            PAGES.append((f"AXS - {fmt_date(iso)}", url))
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
               f"(Plenitude, site officiel, AEG, Ticketmaster, AXS date par date).\n"
               + ("Verification planifiee via GitHub Actions."
                  if RUN_ONCE else
                  f"Verification toutes les {INTERVAL // 60} min."),
               priority="default", tags="white_check_mark")

    failures = {}
    while True:
        debut = time.time()
        avec_axs = axs_a_verifier()
        if avec_axs:
            tour = PAGES
            log(f"--- tour complet ({len(tour)} pages, AXS inclus)")
        else:
            tour = [(l, u) for l, u in PAGES if u not in PAGE_DATE]
            log(f"--- tour annonces ({len(tour)} pages, AXS dans "
                f"<= {AXS_INTERVAL_MIN} min)")
        resultats = fetch_all(tour)
        for label, url in tour:
            check_page(label, url, failures, resultats[url])
        log(f"--- tour termine en {time.time() - debut:.0f}s")
        if RUN_ONCE:
            log("--- termine")
            return
        time.sleep(INTERVAL)


if __name__ == "__main__":
    main()
