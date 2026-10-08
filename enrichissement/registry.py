"""Étape 1 : registre officiel via l'API Recherche d'entreprises (recherche-entreprises.api.gouv.fr)."""

from __future__ import annotations

import sqlite3
import time
from typing import Iterator

import httpx

from .db import now
from .names import display_name

API_URL = "https://recherche-entreprises.api.gouv.fr/search"
PER_PAGE = 25  # maximum autorisé par l'API

EFFECTIFS = {
    "NN": "non renseigné", "00": "0 salarié", "01": "1 ou 2", "02": "3 à 5", "03": "6 à 9",
    "11": "10 à 19", "12": "20 à 49", "21": "50 à 99", "22": "100 à 199", "31": "200 à 249",
    "32": "250 à 499", "41": "500 à 999", "42": "1 000 à 1 999", "51": "2 000 à 4 999",
    "52": "5 000 à 9 999", "53": "10 000 et plus",
}


def fetch_pages(client: httpx.Client, naf: str, departement: str, max_results: int | None = None,
                pause: float = 0.2) -> Iterator[dict]:
    """Itère sur les entreprises actives (paginées), avec backoff sur 429."""
    page, yielded = 1, 0
    while True:
        params = {"activite_principale": naf, "departement": departement,
                  "etat_administratif": "A", "per_page": PER_PAGE, "page": page}
        for attempt in range(5):
            r = client.get(API_URL, params=params)
            if r.status_code == 429:
                time.sleep(2 ** attempt)
                continue
            r.raise_for_status()
            break
        else:
            raise RuntimeError("API Recherche d'entreprises : trop de requêtes (429)")
        data = r.json()
        for raw in data.get("results", []):
            yield raw
            yielded += 1
            if max_results and yielded >= max_results:
                return
        if page >= int(data.get("total_pages") or 0):
            return
        page += 1
        time.sleep(pause)  # l'API autorise ~7 requêtes/s


def parse_company(raw: dict) -> tuple[dict, list[dict]]:
    siege = raw.get("siege") or {}
    code = raw.get("tranche_effectif_salarie") or "NN"
    company = {
        "siren": raw["siren"],
        "raison_sociale": raw.get("nom_raison_sociale") or raw.get("nom_complet") or "",
        "sigle": raw.get("sigle"),
        "adresse": siege.get("adresse"),
        "code_postal": siege.get("code_postal"),
        "commune": siege.get("libelle_commune"),
        "departement": siege.get("departement"),
        "naf": raw.get("activite_principale"),
        "effectif_code": code,
        "effectif_label": EFFECTIFS.get(code, code),
        "date_creation": raw.get("date_creation"),
        "siege_siret": siege.get("siret"),
    }
    persons = []
    for d in raw.get("dirigeants") or []:
        if d.get("type_dirigeant") == "personne morale":
            persons.append({"nom": d.get("denomination") or "", "prenoms": "",
                            "prenom_usuel": "", "qualite": d.get("qualite") or "",
                            "type_dirigeant": "personne morale"})
            continue
        prenoms = (d.get("prenoms") or "").strip()
        persons.append({
            "nom": display_name(d.get("nom") or ""),
            "prenoms": display_name(prenoms),
            "prenom_usuel": display_name(prenoms.split()[0]) if prenoms else "",
            "qualite": d.get("qualite") or "",
            "type_dirigeant": "personne physique",
        })
    return company, [p for p in persons if p["nom"]]


def save_company(conn: sqlite3.Connection, company: dict, persons: list[dict]) -> None:
    ts = now()
    cols = list(company)
    conn.execute(
        f"""INSERT INTO companies ({", ".join(cols)}, collecte_le, maj_le)
            VALUES ({", ".join("?" for _ in cols)}, ?, ?)
            ON CONFLICT (siren) DO UPDATE SET
            {", ".join(f"{c} = excluded.{c}" for c in cols if c != "siren")}, maj_le = excluded.maj_le""",
        [company[c] for c in cols] + [ts, ts],
    )
    for p in persons:
        conn.execute(
            """INSERT INTO persons (siren, nom, prenoms, prenom_usuel, qualite, type_dirigeant, collecte_le)
               VALUES (?, ?, ?, ?, ?, ?, ?)
               ON CONFLICT (siren, nom, prenoms, qualite) DO NOTHING""",
            (company["siren"], p["nom"], p["prenoms"], p["prenom_usuel"], p["qualite"],
             p["type_dirigeant"], ts),
        )


def import_registry(conn: sqlite3.Connection, client: httpx.Client, departement: str,
                    naf: str = "69.20Z", limit: int | None = None) -> int:
    n = 0
    for raw in fetch_pages(client, naf, departement, max_results=limit):
        company, persons = parse_company(raw)
        save_company(conn, company, persons)
        n += 1
    conn.commit()
    return n
