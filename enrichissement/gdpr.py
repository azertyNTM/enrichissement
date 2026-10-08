"""RGPD : liste d'opposition (opt-out) et purge des prospects non convertis."""

from __future__ import annotations

import hashlib
import sqlite3
from datetime import datetime, timedelta, timezone

from .db import now


def email_hash(email: str) -> str:
    return hashlib.sha256(email.strip().lower().encode()).hexdigest()


def is_opted_out(conn: sqlite3.Connection, email: str) -> bool:
    return conn.execute("SELECT 1 FROM optouts WHERE email_hash = ?", (email_hash(email),)).fetchone() is not None


def opt_out_email(conn: sqlite3.Connection, email: str) -> int:
    """Ajoute l'email à la liste repoussoir (hachée, conservée même après purge) et bloque la personne."""
    email = email.strip().lower()
    conn.execute("INSERT OR IGNORE INTO optouts (email_hash, ajoute_le) VALUES (?, ?)", (email_hash(email), now()))
    cur = conn.execute(
        "UPDATE persons SET opt_out = 1 WHERE id IN (SELECT person_id FROM contacts WHERE valeur = ? AND person_id != 0)",
        (email,),
    )
    conn.commit()
    return cur.rowcount


def opt_out_siren(conn: sqlite3.Connection, siren: str) -> int:
    cur = conn.execute("UPDATE companies SET opt_out = 1 WHERE siren = ?", (siren,))
    conn.execute("UPDATE persons SET opt_out = 1 WHERE siren = ?", (siren,))
    conn.commit()
    return cur.rowcount


def mark_converted(conn: sqlite3.Connection, siren: str) -> int:
    cur = conn.execute("UPDATE companies SET converti = 1 WHERE siren = ?", (siren,))
    conn.commit()
    return cur.rowcount


def purge(conn: sqlite3.Connection, years: int = 3, dry_run: bool = False) -> int:
    """Supprime les cabinets non convertis collectés il y a plus de `years` ans (et leurs contacts)."""
    cutoff = (datetime.now(timezone.utc) - timedelta(days=365 * years)).replace(microsecond=0).isoformat()
    sirens = [r[0] for r in conn.execute(
        "SELECT siren FROM companies WHERE converti = 0 AND collecte_le < ?", (cutoff,))]
    if not dry_run and sirens:
        marks = ",".join("?" * len(sirens))
        conn.execute(f"DELETE FROM contacts WHERE siren IN ({marks})", sirens)
        conn.execute(f"DELETE FROM persons WHERE siren IN ({marks})", sirens)
        conn.execute(f"DELETE FROM companies WHERE siren IN ({marks})", sirens)
        conn.commit()
    return len(sirens)
