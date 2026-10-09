"""Base SQLite : chaque donnée est stockée avec sa source, son score et sa date de collecte."""

from __future__ import annotations

import sqlite3
from datetime import datetime, timezone

SCHEMA = """
CREATE TABLE IF NOT EXISTS companies (
    siren              TEXT PRIMARY KEY,
    raison_sociale     TEXT NOT NULL,
    sigle              TEXT,
    adresse            TEXT,
    code_postal        TEXT,
    commune            TEXT,
    departement        TEXT,
    naf                TEXT,
    effectif_code      TEXT,
    effectif_label     TEXT,
    date_creation      TEXT,
    siege_siret        TEXT,
    -- étape 2 : site web validé par le SIREN
    domaine            TEXT,
    domaine_url        TEXT,
    domaine_statut     TEXT NOT NULL DEFAULT 'a_chercher', -- a_chercher | valide | non_trouve
    domaine_preuve     TEXT,  -- URL de la page où le SIREN a été trouvé
    domaine_match      TEXT,  -- siren | siret | tva
    domaine_verifie_le TEXT,
    -- étape 3
    scrape_statut      TEXT,  -- NULL | fait | echec
    scrape_le          TEXT,
    -- RGPD / suivi commercial
    opt_out            INTEGER NOT NULL DEFAULT 0,
    converti           INTEGER NOT NULL DEFAULT 0,
    source             TEXT NOT NULL DEFAULT 'registre',
    collecte_le        TEXT NOT NULL,
    maj_le             TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS persons (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    siren          TEXT NOT NULL REFERENCES companies(siren) ON DELETE CASCADE,
    nom            TEXT NOT NULL,
    prenoms        TEXT NOT NULL DEFAULT '',
    prenom_usuel   TEXT NOT NULL DEFAULT '',
    qualite        TEXT NOT NULL DEFAULT '',
    type_dirigeant TEXT NOT NULL DEFAULT 'personne physique',
    principal      INTEGER NOT NULL DEFAULT 0,  -- 1 = celui qui tient les rênes (seul ciblé)
    opt_out        INTEGER NOT NULL DEFAULT 0,
    email_statut   TEXT,  -- NULL | trouve_site | devine | aucun_valide | pas_de_mx | bounce
    linkedin_url    TEXT,
    linkedin_titre  TEXT,  -- titre du résultat de recherche (preuve)
    linkedin_score  INTEGER,
    linkedin_statut TEXT,  -- NULL | trouve | non_trouve
    linkedin_le     TEXT,
    source         TEXT NOT NULL DEFAULT 'registre',
    collecte_le    TEXT NOT NULL,
    UNIQUE (siren, nom, prenoms, qualite)
);

CREATE TABLE IF NOT EXISTS contacts (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    siren        TEXT NOT NULL REFERENCES companies(siren) ON DELETE CASCADE,
    person_id    INTEGER NOT NULL DEFAULT 0,  -- 0 = rattaché au cabinet
    type         TEXT NOT NULL,  -- email_dirigeant | email_cabinet | email_nominatif | telephone
    valeur       TEXT NOT NULL,
    source       TEXT NOT NULL,  -- site | devine
    url_source   TEXT,
    motif        TEXT,
    score        INTEGER NOT NULL,
    statut_verif TEXT NOT NULL DEFAULT 'non_verifie', -- non_verifie | valid | invalid | catch_all | unknown | bounced
    collecte_le  TEXT NOT NULL,
    verifie_le   TEXT,
    UNIQUE (siren, person_id, type, valeur)
);

CREATE TABLE IF NOT EXISTS domains (
    domaine       TEXT PRIMARY KEY,
    mx_hosts      TEXT,
    mx_provider   TEXT,
    catch_all     INTEGER,  -- NULL = inconnu, 0 = non, 1 = oui
    smtp_fiable   INTEGER NOT NULL DEFAULT 1,
    motif         TEXT,
    motif_source  TEXT,     -- site | smtp
    motif_statut  TEXT NOT NULL DEFAULT 'actif', -- actif | invalide
    motif_bounces INTEGER NOT NULL DEFAULT 0,
    verifie_le    TEXT
);

CREATE TABLE IF NOT EXISTS optouts (
    email_hash TEXT PRIMARY KEY,  -- sha256 de l'email normalisé (liste repoussoir)
    ajoute_le  TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS smtp_checks (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    domaine    TEXT NOT NULL,
    adresse    TEXT NOT NULL,
    code       INTEGER,
    resultat   TEXT NOT NULL,
    checked_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS runs (
    id     INTEGER PRIMARY KEY AUTOINCREMENT,
    etape  TEXT NOT NULL,
    debut  TEXT NOT NULL,
    fin    TEXT,
    stats  TEXT
);

CREATE INDEX IF NOT EXISTS idx_contacts_valeur ON contacts(valeur);
CREATE INDEX IF NOT EXISTS idx_persons_siren ON persons(siren);
"""


def now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def connect(path: str = "enrichissement.db") -> sqlite3.Connection:
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.executescript(SCHEMA)
    return conn


def upsert_contact(
    conn: sqlite3.Connection,
    *,
    siren: str,
    type: str,
    valeur: str,
    source: str,
    score: int,
    person_id: int = 0,
    url_source: str | None = None,
    motif: str | None = None,
    statut_verif: str = "non_verifie",
) -> None:
    """Insère un contact ou met à jour sa vérification. Un email « bounced » reste à 0."""
    ts = now()
    conn.execute(
        """
        INSERT INTO contacts (siren, person_id, type, valeur, source, url_source, motif,
                              score, statut_verif, collecte_le, verifie_le)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT (siren, person_id, type, valeur) DO UPDATE SET
            score        = CASE WHEN contacts.statut_verif = 'bounced' THEN 0 ELSE excluded.score END,
            statut_verif = CASE WHEN contacts.statut_verif = 'bounced' THEN 'bounced' ELSE excluded.statut_verif END,
            source       = CASE WHEN contacts.source = 'site' THEN 'site' ELSE excluded.source END,
            url_source   = COALESCE(contacts.url_source, excluded.url_source),
            motif        = COALESCE(excluded.motif, contacts.motif),
            verifie_le   = excluded.verifie_le
        """,
        (siren, person_id, type, valeur, source, url_source, motif, score, statut_verif, ts,
         ts if statut_verif != "non_verifie" else None),
    )


def get_domain(conn: sqlite3.Connection, domaine: str) -> sqlite3.Row | None:
    return conn.execute("SELECT * FROM domains WHERE domaine = ?", (domaine,)).fetchone()


def ensure_domain(conn: sqlite3.Connection, domaine: str) -> sqlite3.Row:
    conn.execute("INSERT OR IGNORE INTO domains (domaine) VALUES (?)", (domaine,))
    return get_domain(conn, domaine)
