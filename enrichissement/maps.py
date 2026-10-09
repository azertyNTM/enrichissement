"""Étape 3b : standard du cabinet via sa fiche Google Maps, pour ceux dont le site n'a rien donné.

Règle : une fiche n'est retenue que si son adresse porte le code postal du siège (registre)
ET si son nom correspond au cabinet. Sinon on la rejette (agence d'un autre département, homonyme…).
"""

from __future__ import annotations

import re
import sqlite3
from typing import Callable

from . import scoring
from .db import now, upsert_contact
from .linkedin import company_tokens, norm
from .names import ascii_lower
from .scrape import normalize_phone


def compact(s: str) -> str:
    """'A.c.c.a.l.' -> 'accal'."""
    return re.sub(r"[^a-z0-9]", "", ascii_lower(s))


# un seul mot du nom ne suffit que si la fiche indique aussi le métier
JOB_HINTS = re.compile(r"expert|comptab|fiduciaire|audit|cabinet", re.IGNORECASE)


def matches(place: dict, company: sqlite3.Row) -> bool:
    if not company["code_postal"] or company["code_postal"] not in (place.get("address") or ""):
        return False
    title = place.get("title") or ""
    words = set(norm(title).split()) | {compact(title)}  # « A.c.c.a.l. » -> accal
    sigle = compact(company["sigle"] or "")
    if len(sigle) >= 3 and sigle in words:  # fiche au nom du sigle seul : « C.D.E. »
        return True
    tokens = company_tokens(company["raison_sociale"], None)
    if not tokens:  # « AB COMPTA CONSEIL » : que des mots génériques
        return compact(company["raison_sociale"]) in compact(title)
    found = tokens & words
    return found == tokens or (bool(found) and bool(JOB_HINTS.search(title)))


def run(conn: sqlite3.Connection, places: Callable[[str], list[dict]], limit: int | None = None) -> dict:
    rows = conn.execute(
        "SELECT * FROM companies c WHERE opt_out = 0 AND maps_statut IS NULL AND NOT EXISTS "
        "(SELECT 1 FROM contacts WHERE siren = c.siren AND type = 'telephone') ORDER BY siren"
        + (" LIMIT ?" if limit else ""), (limit,) if limit else ()).fetchall()
    stats = {"traites": 0, "trouves": 0, "non_trouves": 0}
    for company in rows:
        found = None
        for place in places(f"{company['raison_sociale']} {company['commune'] or ''}"):
            phone = normalize_phone(place.get("phoneNumber") or "")
            if phone and matches(place, company):
                found = (phone, f"Google Maps : {place.get('title')}, {place.get('address')}")
                break
        if found:
            upsert_contact(conn, siren=company["siren"], type="telephone", valeur=found[0],
                           source="google_maps", score=scoring.TELEPHONE_MAPS, url_source=found[1])
            stats["trouves"] += 1
        else:
            stats["non_trouves"] += 1
        conn.execute("UPDATE companies SET maps_statut = ?, maj_le = ? WHERE siren = ?",
                     ("trouve" if found else "non_trouve", now(), company["siren"]))
        stats["traites"] += 1
        conn.commit()
    return stats
