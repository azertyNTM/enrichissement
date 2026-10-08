"""Étape 3 : extraire ce qui est publié sur le site validé (téléphone, emails, motif d'email)."""

from __future__ import annotations

import re
import sqlite3
from urllib.parse import unquote, urljoin

from bs4 import BeautifulSoup

from . import scoring
from .db import ensure_domain, now, upsert_contact
from .fetch import Fetcher, host_of
from .names import detect_pattern, first_name_candidates
from .website import html_text, legal_links

PAGE_HINTS = re.compile(r"contact|[eé]quipe|team|cabinet|qui[\s\-_]*sommes|a[\s\-_]*propos|"
                        r"nous[\s\-_]*conna[iî]tre|associ[eé]s|collaborateurs|implantation|agence",
                        re.IGNORECASE)
MAX_PAGES = 6

EMAIL_RE = re.compile(r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,24}")
OBFUSCATED_RE = re.compile(
    r"([A-Za-z0-9._%+\-]+)\s*(?:\[at\]|\(at\)|\{at\}|\s+at\s+|\[arobase\]|\(arobase\)|\s+arobase\s+)\s*"
    r"([A-Za-z0-9\-]+(?:\s*(?:\.|\[dot\]|\(dot\)|\s+point\s+)\s*[A-Za-z0-9\-]+)+)",
    re.IGNORECASE,
)
FILE_EXT = (".png", ".jpg", ".jpeg", ".gif", ".webp", ".svg", ".css", ".js")

PHONE_RE = re.compile(r"(?:(?:\+|00)\s*33\s*\(?0?\)?\s*|\b0)[1-9](?:[\s.\-]*\d{2}){4}\b")

GENERIC_LOCALS = {
    "contact", "info", "infos", "information", "accueil", "cabinet", "secretariat",
    "bonjour", "hello", "admin", "administration", "compta", "comptabilite", "social", "paie",
    "juridique", "fiscal", "direction", "rh", "recrutement", "emploi", "jobs", "candidature",
    "office", "mail", "courrier", "standard", "expertise", "audit", "conseil", "facturation",
    "webmaster", "noreply", "no-reply", "nepasrepondre", "dpo", "rgpd", "support", "commercial",
}
FREE_MAIL = {"orange.fr", "wanadoo.fr", "free.fr", "sfr.fr", "laposte.net", "gmail.com",
             "hotmail.fr", "hotmail.com", "outlook.fr", "outlook.com", "yahoo.fr", "yahoo.com",
             "neuf.fr", "bbox.fr", "aliceadsl.fr", "live.fr", "icloud.com"}


# ---------- extraction pure (testable sans réseau) ----------

def extract_emails(html: str) -> set[str]:
    soup = BeautifulSoup(html, "html.parser")
    found: set[str] = set()
    for a in soup.find_all("a", href=True):
        href = a["href"]
        if href.lower().startswith("mailto:"):
            addr = unquote(href[7:].split("?")[0]).strip()
            found.update(EMAIL_RE.findall(addr))
    text = html_text(html)
    found.update(EMAIL_RE.findall(text))
    for local, dom in OBFUSCATED_RE.findall(text):
        dom = re.sub(r"\s*(?:\[dot\]|\(dot\)|\s+point\s+|\.)\s*", ".", dom, flags=re.IGNORECASE)
        candidate = f"{local}@{dom}"
        if EMAIL_RE.fullmatch(candidate):
            found.add(candidate)
    return {e.lower().strip(".") for e in found if not e.lower().endswith(FILE_EXT)}


def normalize_phone(raw: str) -> str | None:
    digits = re.sub(r"\D", "", raw)
    if digits.startswith("0033"):
        digits = digits[4:]
    elif digits.startswith("33") and len(digits) >= 11:
        digits = digits[2:]
    digits = digits.lstrip("0")
    if len(digits) != 9:
        return None
    return "+33" + digits


def extract_phones(text: str) -> list[str]:
    """Numéros français normalisés E.164, en ignorant les numéros de fax. Fixes avant mobiles."""
    out: list[str] = []
    for m in PHONE_RE.finditer(text):
        before = text[max(0, m.start() - 20):m.start()].lower()
        if "fax" in before or "télécopie" in before or "telecopie" in before:
            continue
        num = normalize_phone(m.group(0))
        if num and num not in out:
            out.append(num)
    return sorted(out, key=lambda n: n[3] in "67")  # mobiles (06/07) en dernier


def is_generic(local: str) -> bool:
    base = re.split(r"[.\-_+]", local)[0]
    return local in GENERIC_LOCALS or base in GENERIC_LOCALS


def email_domain_ok(email: str, site_domain: str) -> str | None:
    """'propre' si l'email est sur le domaine du site, 'gratuit' si webmail, sinon None (tiers)."""
    dom = email.split("@", 1)[1]
    if dom == site_domain or dom.endswith("." + site_domain):
        return "propre"
    if dom in FREE_MAIL:
        return "gratuit"
    return None


def interesting_links(html: str, base_url: str) -> list[str]:
    soup = BeautifulSoup(html, "html.parser")
    out: list[str] = []
    base_host = host_of(base_url)
    for a in soup.find_all("a", href=True):
        if PAGE_HINTS.search(f"{a.get_text(' ')} {a['href']}"):
            url = urljoin(base_url, a["href"]).split("#")[0]
            if host_of(url) == base_host and url not in out and not url.lower().endswith(FILE_EXT):
                out.append(url)
    return out


# ---------- orchestration ----------

def scrape_company(conn: sqlite3.Connection, fetcher: Fetcher, company: sqlite3.Row) -> dict:
    siren, domain = company["siren"], company["domaine"]
    stats = {"pages": 0, "telephone": False, "emails_cabinet": 0, "emails_dirigeants": 0}
    home = fetcher.get(company["domaine_url"])
    if home is None:
        return stats
    urls = [company["domaine_preuve"]] + legal_links(home.html, home.url) + interesting_links(home.html, home.url)
    pages = [home]
    for url in dict.fromkeys(u for u in urls if u and u != home.url):
        if len(pages) >= MAX_PAGES:
            break
        p = fetcher.get(url)
        if p and host_of(p.url) == host_of(home.url):
            pages.append(p)
    stats["pages"] = len(pages)

    persons = conn.execute("SELECT * FROM persons WHERE siren = ? AND type_dirigeant = 'personne physique'",
                           (siren,)).fetchall()
    phone_done = False
    legal_urls = {company["domaine_preuve"], *legal_links(home.html, home.url)}
    for page in sorted(pages, key=lambda p: _page_rank(p.url, legal_urls)):
        text = html_text(page.html)
        if not phone_done:
            if page.url in legal_urls:
                text_phone = _before_hosting_section(text)
            else:
                text_phone = text
            phones = extract_phones(text_phone)
            if phones:
                upsert_contact(conn, siren=siren, type="telephone", valeur=phones[0], source="site",
                               score=scoring.TELEPHONE_SITE, url_source=page.url)
                phone_done = stats["telephone"] = True
        for email in extract_emails(page.html):
            kind = email_domain_ok(email, domain)
            if kind is None:
                continue  # email d'un tiers (hébergeur, agence web…)
            local = email.split("@", 1)[0]
            if is_generic(local) or kind == "gratuit" and not _matches_any(local, persons):
                score = scoring.EMAIL_CABINET if kind == "propre" else scoring.EMAIL_CABINET_GRATUIT
                upsert_contact(conn, siren=siren, type="email_cabinet", valeur=email, source="site",
                               score=score, url_source=page.url)
                stats["emails_cabinet"] += 1
                continue
            matched = False
            for person in persons:
                pattern, prenom = _match_person(local, person)
                if pattern:
                    matched = True
                    if prenom != person["prenom_usuel"]:
                        # le site révèle le prénom usuel composé (ex. registre 'Jean Pierre' -> 'Jean-Pierre')
                        conn.execute("UPDATE persons SET prenom_usuel = ? WHERE id = ?", (prenom, person["id"]))
                    upsert_contact(conn, siren=siren, person_id=person["id"], type="email_dirigeant",
                                   valeur=email, source="site", score=scoring.EMAIL_SITE_DIRIGEANT,
                                   url_source=page.url, motif=pattern)
                    conn.execute("UPDATE persons SET email_statut = 'trouve_site' WHERE id = ?", (person["id"],))
                    if kind == "propre":
                        _record_pattern(conn, domain, pattern, "site")
                    stats["emails_dirigeants"] += 1
            if not matched:
                upsert_contact(conn, siren=siren, type="email_nominatif", valeur=email, source="site",
                               score=scoring.EMAIL_NOMINATIF, url_source=page.url)
    return stats


def _page_rank(url: str, legal_urls: set) -> int:
    """Ordre de lecture pour le téléphone : contact, autres pages, mentions légales en dernier."""
    if "contact" in url.lower():
        return 0
    return 2 if url in legal_urls else 1


def _before_hosting_section(text: str) -> str:
    """Sur les mentions légales, ignore le bloc « hébergeur » (téléphone d'OVH, d'o2switch…)."""
    m = re.search(r"h[ée]berg", text, re.IGNORECASE)
    return text[:m.start()] if m else text


def _match_person(local: str, person) -> tuple[str | None, str | None]:
    # prénom usuel d'abord ; le composé n'est retenu que si l'email l'écrit en entier
    for prenom in first_name_candidates(person["prenom_usuel"], person["prenoms"]):
        pattern = detect_pattern(local, prenom, person["nom"])
        if pattern:
            return pattern, prenom
    return None, None


def _matches_any(local: str, persons) -> bool:
    return any(_match_person(local, p)[0] for p in persons)


def _record_pattern(conn: sqlite3.Connection, domain: str, pattern: str, source: str) -> None:
    row = ensure_domain(conn, domain)
    if row["motif_statut"] == "invalide" and row["motif"] == pattern:
        return
    if row["motif"] is None or row["motif_source"] != "site" or row["motif_statut"] == "invalide":
        conn.execute("UPDATE domains SET motif = ?, motif_source = ?, motif_statut = 'actif', "
                     "motif_bounces = 0 WHERE domaine = ?", (pattern, source, domain))


def run(conn: sqlite3.Connection, fetcher: Fetcher, limit: int | None = None) -> dict:
    rows = conn.execute(
        "SELECT * FROM companies WHERE opt_out = 0 AND domaine_statut = 'valide' AND scrape_statut IS NULL"
        " ORDER BY siren" + (" LIMIT ?" if limit else ""), (limit,) if limit else (),
    ).fetchall()
    total = {"traites": 0, "avec_telephone": 0, "emails_cabinet": 0, "emails_dirigeants": 0}
    for company in rows:
        s = scrape_company(conn, fetcher, company)
        statut = "fait" if s["pages"] else "echec"
        conn.execute("UPDATE companies SET scrape_statut = ?, scrape_le = ? WHERE siren = ?",
                     (statut, now(), company["siren"]))
        conn.commit()
        total["traites"] += 1
        total["avec_telephone"] += int(s["telephone"])
        total["emails_cabinet"] += s["emails_cabinet"]
        total["emails_dirigeants"] += s["emails_dirigeants"]
    return total
