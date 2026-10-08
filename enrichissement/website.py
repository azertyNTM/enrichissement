"""Étape 2b : trouver le site du cabinet et le valider par la présence du SIREN.

Règle : un domaine n'est accepté que si le SIREN (ou le SIRET du cabinet, ou son n° de TVA
intracommunautaire) figure sur une page de ce domaine (mentions légales en priorité).
Sinon le domaine est rejeté : on préfère « non trouvé » à un mauvais site.
"""

from __future__ import annotations

import re
import sqlite3
from urllib.parse import urljoin, urlsplit

from bs4 import BeautifulSoup

from .db import now
from .fetch import Fetcher, host_of
from .search import SearchProvider

# Annuaires, réseaux sociaux, registres : ils affichent le SIREN mais ne sont pas le site du cabinet.
BLOCKLIST = {
    "pappers.fr", "societe.com", "verif.com", "infogreffe.fr", "manageo.fr", "corporama.com",
    "kompass.com", "infonet.fr", "b-reputation.com", "score3.fr", "societeinfo.com", "figaro.fr",
    "lefigaro.fr", "pagesjaunes.fr", "pagesblanches.fr", "118712.fr", "118000.fr", "cylex.fr",
    "cylex-france.fr", "justacote.com", "hoodspot.fr", "mappy.com", "yelp.fr", "yelp.com",
    "linkedin.com", "facebook.com", "instagram.com", "twitter.com", "x.com", "youtube.com",
    "google.com", "google.fr", "wikipedia.org", "gouv.fr", "data.gouv.fr", "bodacc.fr",
    "experts-comptables.fr", "experts-comptables.org", "oec-paris.fr", "trustpilot.com",
    "indeed.com", "indeed.fr", "hellowork.com", "welcometothejungle.com", "doctrine.fr",
    "annuaire-entreprises.data.gouv.fr", "entreprises.lefigaro.fr", "dirigeant.societe.com",
    "lesechos.fr", "francebleu.fr", "ledauphine.com", "waze.com", "tripadvisor.fr",
    "fr.kompass.com", "annuaire.laposte.fr", "comptable-en-ligne.fr", "expert-comptable.fr",
    "pages-jaunes.fr", "lagazette-drome-ardeche.fr", "entreprises.lesechos.fr", "verif.fr",
    "net-entreprises.fr", "rubypayeur.com", "dun-bradstreet.fr", "dnb.com", "zoominfo.com",
    "rocketreach.co", "apollo.io", "lusha.com", "kaspr.io",
}

LEGAL_HINTS = re.compile(r"mentions?[\s\-_]*l[eé]gales?|informations?[\s\-_]*l[eé]gales?|"
                         r"\blegal\b|l[eé]gal|cgu|conditions[\s\-_]*g[eé]n[eé]rales|impressum",
                         re.IGNORECASE)
LEGAL_FALLBACK_PATHS = ["/mentions-legales", "/mentions-legales/", "/mentions-legales.html",
                        "/legal", "/informations-legales"]
MAX_CANDIDATES = 5


def is_blocked(host: str) -> bool:
    return any(host == b or host.endswith("." + b) for b in BLOCKLIST)


def normalize_digits(text: str) -> str:
    """Supprime les séparateurs entre chiffres : '123 456 789' / '123.456.789' -> '123456789'."""
    return re.sub(r"(?<=\d)[\s  .\-]+(?=\d)", "", text)


def find_siren(text: str, siren: str) -> str | None:
    """Retourne 'siren', 'siret' ou 'tva' si l'identifiant du cabinet apparaît dans le texte."""
    t = normalize_digits(text)
    if re.search(rf"FR\s*[0-9A-Z]{{2}}{siren}(?!\d)", t, re.IGNORECASE):
        return "tva"
    m = re.search(rf"(?<!\d){siren}(\d{{5}})?(?!\d)", t)
    if m:
        return "siret" if m.group(1) else "siren"
    return None


def html_text(html: str) -> str:
    soup = BeautifulSoup(html, "html.parser")
    for tag in soup(["script", "style", "noscript"]):
        tag.decompose()
    return soup.get_text(" ")


def legal_links(html: str, base_url: str) -> list[str]:
    soup = BeautifulSoup(html, "html.parser")
    out = []
    base_host = host_of(base_url)
    for a in soup.find_all("a", href=True):
        label = f"{a.get_text(' ')} {a['href']}"
        if LEGAL_HINTS.search(label):
            url = urljoin(base_url, a["href"]).split("#")[0]
            if host_of(url) == base_host and url not in out:
                out.append(url)
    return out[:3]


def candidate_sites(results) -> list[str]:
    """Une URL racine par domaine, sans annuaires, dans l'ordre des résultats."""
    seen, out = set(), []
    for r in results:
        parts = urlsplit(r.url)
        if parts.scheme not in ("http", "https"):
            continue
        host = host_of(r.url)
        if not host or is_blocked(host) or host in seen:
            continue
        seen.add(host)
        out.append(f"{parts.scheme}://{parts.netloc}/")
    return out


def validate_site(fetcher: Fetcher, root_url: str, siren: str) -> dict | None:
    """Cherche le SIREN sur la home puis les pages légales. Retourne la preuve ou None."""
    home = fetcher.get(root_url)
    if home is None:
        return None
    final_root = f"{urlsplit(home.url).scheme}://{urlsplit(home.url).netloc}/"
    pages = legal_links(home.html, home.url)
    if not pages:
        pages = [urljoin(final_root, p) for p in LEGAL_FALLBACK_PATHS]
    # les pages légales d'abord : c'est là que l'obligation d'affichage s'applique
    for url in pages:
        page = fetcher.get(url)
        if page and host_of(page.url) == host_of(home.url):
            match = find_siren(html_text(page.html), siren)
            if match:
                return {"domaine": host_of(home.url), "domaine_url": final_root,
                        "domaine_preuve": page.url, "domaine_match": match}
    match = find_siren(html_text(home.html), siren)
    if match:
        return {"domaine": host_of(home.url), "domaine_url": final_root,
                "domaine_preuve": home.url, "domaine_match": match}
    return None


def build_queries(company: sqlite3.Row) -> list[str]:
    name, commune = company["raison_sociale"], company["commune"] or ""
    s = company["siren"]
    return [
        f"{name} {commune} expert-comptable",
        f"\"{s[:3]} {s[3:6]} {s[6:]}\" OR \"{s}\" mentions légales",
    ]


def find_website(conn: sqlite3.Connection, fetcher: Fetcher, provider: SearchProvider,
                 company: sqlite3.Row) -> dict | None:
    tried: set[str] = set()
    for query in build_queries(company):
        try:
            results = provider.search(query)
        except Exception:  # noqa: BLE001 - une erreur d'API ne doit pas arrêter le lot
            continue
        for root in candidate_sites(results)[:MAX_CANDIDATES]:
            if root in tried:
                continue
            tried.add(root)
            proof = validate_site(fetcher, root, company["siren"])
            if proof:
                return proof
    return None


def run(conn: sqlite3.Connection, fetcher: Fetcher, provider: SearchProvider,
        limit: int | None = None, retry: bool = False) -> dict:
    statuts = ("a_chercher", "non_trouve") if retry else ("a_chercher",)
    rows = conn.execute(
        f"SELECT * FROM companies WHERE opt_out = 0 AND domaine_statut IN ({','.join('?' * len(statuts))})"
        " ORDER BY siren" + (" LIMIT ?" if limit else ""),
        (*statuts, limit) if limit else statuts,
    ).fetchall()
    stats = {"traites": 0, "valides": 0, "non_trouves": 0}
    for company in rows:
        proof = find_website(conn, fetcher, provider, company)
        ts = now()
        if proof:
            conn.execute(
                """UPDATE companies SET domaine = ?, domaine_url = ?, domaine_preuve = ?, domaine_match = ?,
                   domaine_statut = 'valide', domaine_verifie_le = ?, scrape_statut = NULL, maj_le = ?
                   WHERE siren = ?""",
                (proof["domaine"], proof["domaine_url"], proof["domaine_preuve"], proof["domaine_match"],
                 ts, ts, company["siren"]),
            )
            stats["valides"] += 1
        else:
            conn.execute("UPDATE companies SET domaine_statut = 'non_trouve', domaine_verifie_le = ?, "
                         "maj_le = ? WHERE siren = ?", (ts, ts, company["siren"]))
            stats["non_trouves"] += 1
        stats["traites"] += 1
        conn.commit()
    return stats
