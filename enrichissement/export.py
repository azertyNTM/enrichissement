"""Export CSV (une ligne par dirigeant) importable dans Google Sheets / Instantly."""

from __future__ import annotations

import csv
import sqlite3
from pathlib import Path
from urllib.parse import quote_plus

from .gdpr import is_opted_out

COLUMNS = [
    "siren", "raison_sociale", "commune", "code_postal", "effectif", "site_web", "site_preuve_siren",
    "telephone", "telephone_source", "email_cabinet", "email_cabinet_score",
    "prenom", "nom", "qualite", "email", "email_score", "email_source", "email_statut", "email_url_source",
    "linkedin_recherche", "source_dirigeant", "collecte_le",
]


def linkedin_search_url(prenom: str, nom: str, raison_sociale: str) -> str:
    return "https://www.linkedin.com/search/results/people/?keywords=" + quote_plus(f"{prenom} {nom} {raison_sociale}")


def _best(conn, siren, type_, person_id=0):
    return conn.execute(
        "SELECT * FROM contacts WHERE siren = ? AND type = ? AND person_id = ? AND score > 0 "
        "ORDER BY score DESC, id LIMIT 1", (siren, type_, person_id)).fetchone()


def rows(conn: sqlite3.Connection, min_score: int, include_all: bool = False) -> list[dict]:
    out = []
    companies = conn.execute("SELECT * FROM companies WHERE opt_out = 0 ORDER BY commune, raison_sociale").fetchall()
    for c in companies:
        tel = _best(conn, c["siren"], "telephone")
        cab = _best(conn, c["siren"], "email_cabinet")
        if cab is not None and is_opted_out(conn, cab["valeur"]):
            cab = None
        persons = conn.execute(
            "SELECT * FROM persons WHERE siren = ? AND type_dirigeant = 'personne physique' AND opt_out = 0",
            (c["siren"],)).fetchall()
        for p in persons:
            email = _best(conn, c["siren"], "email_dirigeant", p["id"])
            if email is not None and is_opted_out(conn, email["valeur"]):
                email = None
            ok = email is not None and email["score"] >= min_score
            if not ok and not include_all:
                continue
            out.append({
                "siren": c["siren"], "raison_sociale": c["raison_sociale"], "commune": c["commune"],
                "code_postal": c["code_postal"], "effectif": c["effectif_label"],
                "site_web": c["domaine_url"] if c["domaine_statut"] == "valide" else "",
                "site_preuve_siren": c["domaine_preuve"] or "",
                "telephone": tel["valeur"] if tel else "", "telephone_source": tel["url_source"] if tel else "",
                "email_cabinet": cab["valeur"] if cab else "", "email_cabinet_score": cab["score"] if cab else "",
                "prenom": p["prenom_usuel"], "nom": p["nom"], "qualite": p["qualite"],
                "email": email["valeur"] if ok else "",
                "email_score": email["score"] if email else "",
                "email_source": email["source"] if email else "",
                "email_statut": email["statut_verif"] if email else (p["email_statut"] or ""),
                "email_url_source": (email["url_source"] or "") if email else "",
                "linkedin_recherche": linkedin_search_url(p["prenom_usuel"], p["nom"], c["raison_sociale"]),
                "source_dirigeant": "registre (recherche-entreprises.api.gouv.fr)",
                "collecte_le": p["collecte_le"],
            })
    return out


def write_csv(conn: sqlite3.Connection, path: str | Path, min_score: int, include_all: bool = False) -> int:
    data = rows(conn, min_score, include_all)
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=COLUMNS)
        w.writeheader()
        w.writerows(data)
    return len(data)
