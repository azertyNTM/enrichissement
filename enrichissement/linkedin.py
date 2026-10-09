"""Étape 2c : profil LinkedIn du dirigeant, trouvé par recherche web et validé.

Règle : un profil n'est retenu que si son titre contient le prénom ET le nom du dirigeant.
Le score dépend de ce qui confirme que c'est bien lui :
  90 = le nom du cabinet apparaît dans le titre / l'extrait
  70 = seulement la commune ou le métier (expert-comptable)
Sinon : « non trouvé ». On préfère pas de profil à un homonyme.
"""

from __future__ import annotations

import re
import sqlite3
from urllib.parse import urlsplit

from .db import now
from .names import ascii_lower, first_name_variants, last_name_variants
from .search import SearchProvider

# Mots trop communs dans les raisons sociales pour prouver quoi que ce soit.
GENERIC_WORDS = {
    "cabinet", "expertise", "expert", "experts", "comptable", "comptables", "compta", "comptabilite", "conseil",
    "conseils", "audit", "gestion", "associes", "associe", "partenaires", "societe", "sarl", "sas",
    "sasu", "selarl", "selas", "eurl", "scp", "groupe", "france", "et", "de", "du", "des", "la", "le",
    "les", "en", "and", "fiduciaire", "entreprise", "entreprises", "office",
}

JOB_HINTS = re.compile(r"expert[\s\-]*comptable|comptab|commissaire aux comptes", re.IGNORECASE)


def norm(s: str) -> str:
    return " " + re.sub(r"[^a-z0-9]+", " ", ascii_lower(s)) + " "


def is_profile_url(url: str) -> bool:
    parts = urlsplit(url)
    host = parts.netloc.lower()
    return (host == "linkedin.com" or host.endswith(".linkedin.com")) and parts.path.startswith("/in/")


def clean_profile_url(url: str) -> str:
    """'https://fr.linkedin.com/in/jean-dupont-12ab/?trk=x' -> 'https://www.linkedin.com/in/jean-dupont-12ab'."""
    slug = urlsplit(url).path.split("/")[2]
    return f"https://www.linkedin.com/in/{slug}"


def company_tokens(raison_sociale: str, sigle: str | None) -> set[str]:
    words = norm(raison_sociale).split() + norm(sigle or "").split()
    return {w for w in words if len(w) >= 3 and w not in GENERIC_WORDS}


def name_in(text: str, prenom: str, nom: str) -> bool:
    t = norm(text)
    has_first = any(norm(f.replace("-", " ")) in t for f in first_name_variants(prenom))
    has_last = any(norm(l.replace("-", " ")) in t for l in last_name_variants(nom))
    return has_first and has_last


def score_result(title: str, snippet: str, prenom: str, nom: str, tokens: set[str], commune: str) -> int:
    # titre LinkedIn = « Prénom Nom - Poste » : le nom doit être celui du titulaire, pas du cabinet cité après
    if not name_in(re.split(r"\s[-–|]\s", title)[0], prenom, nom):
        return 0
    text = norm(f"{title} {snippet}")
    # le nom du dirigeant figure souvent dans la raison sociale (« Cabinet Gilles Deves ») : il ne prouve rien
    proof = tokens - set(norm(f"{prenom} {nom}").split())
    if any(f" {w} " in text for w in proof):
        return 90
    if (commune and norm(commune) in text) or JOB_HINTS.search(f"{title} {snippet}"):
        return 70
    return 0


def build_queries(prenom: str, nom: str, raison_sociale: str, commune: str) -> list[str]:
    return [
        f'site:linkedin.com/in "{prenom} {nom}" {raison_sociale}',
        f'site:linkedin.com/in "{prenom} {nom}" expert-comptable {commune}',
    ]


def find_profile(provider: SearchProvider, person: sqlite3.Row) -> dict | None:
    # « Granon (Colombet) » : le registre ajoute le nom d'usage entre parenthèses
    prenom, nom = person["prenom_usuel"], re.sub(r"\s*\(.*?\)", "", person["nom"]).strip()
    tokens = company_tokens(person["raison_sociale"], person["sigle"])
    best = None
    for query in build_queries(prenom, nom, person["raison_sociale"], person["commune"] or ""):
        results = provider.search(query)  # une erreur d'API arrête le lot (reprenable) au lieu de tout classer « non trouvé »
        for r in results:
            if not is_profile_url(r.url):
                continue
            score = score_result(r.title, r.snippet, prenom, nom, tokens, person["commune"] or "")
            if score and (best is None or score > best["score"]):
                best = {"url": clean_profile_url(r.url), "titre": r.title, "score": score}
        if best and best["score"] == 90:
            break
    return best


def run(conn: sqlite3.Connection, provider: SearchProvider, limit: int | None = None,
        retry: bool = False) -> dict:
    statut = "(p.linkedin_statut IS NULL OR p.linkedin_statut = 'non_trouve')" if retry \
        else "p.linkedin_statut IS NULL"
    rows = conn.execute(
        "SELECT p.*, c.raison_sociale, c.sigle, c.commune FROM persons p JOIN companies c USING (siren) "
        f"WHERE {statut} AND p.principal = 1 AND p.opt_out = 0 AND c.opt_out = 0 "
        "AND p.prenom_usuel != '' ORDER BY p.siren, p.id" + (" LIMIT ?" if limit else ""),
        (limit,) if limit else (),
    ).fetchall()
    stats = {"traites": 0, "trouves": 0, "non_trouves": 0}
    for person in rows:
        profile = find_profile(provider, person)
        if profile:
            conn.execute("UPDATE persons SET linkedin_url = ?, linkedin_titre = ?, linkedin_score = ?, "
                         "linkedin_statut = 'trouve', linkedin_le = ? WHERE id = ?",
                         (profile["url"], profile["titre"], profile["score"], now(), person["id"]))
            stats["trouves"] += 1
        else:
            conn.execute("UPDATE persons SET linkedin_statut = 'non_trouve', linkedin_le = ? WHERE id = ?",
                         (now(), person["id"]))
            stats["non_trouves"] += 1
        stats["traites"] += 1
        conn.commit()
    conn.commit()
    return stats
