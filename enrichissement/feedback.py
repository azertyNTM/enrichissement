"""Boucle de retour : les rebonds (bounces) exportés d'Instantly dégradent la base.

- l'email qui rebondit passe à 0 (statut 'bounced') et n'est plus jamais exporté ;
- si c'était un email deviné avec le motif du domaine, le motif perd en crédibilité :
  après MOTIF_MAX_BOUNCES rebonds, il est invalidé et les autres emails devinés avec ce motif
  sont rétrogradés au score « deviné sans motif » ;
- si l'email avait été validé par SMTP, le serveur du domaine n'est plus jugé fiable :
  ses autres « valides » SMTP sont rétrogradés.
"""

from __future__ import annotations

import csv
import sqlite3
from pathlib import Path

from . import scoring
from .db import now

MOTIF_MAX_BOUNCES = 2
BOUNCE_WORDS = ("bounce", "rebond", "invalid", "hard")


def read_bounces(path: str | Path) -> list[str]:
    """Lit un CSV Instantly (ou tout CSV avec une colonne email). Si une colonne de statut existe,
    seules les lignes dont le statut évoque un rebond sont retenues."""
    with open(path, newline="", encoding="utf-8-sig") as f:
        sample = f.read(4096)
        f.seek(0)
        try:
            dialect = csv.Sniffer().sniff(sample, delimiters=",;\t")
        except csv.Error:
            dialect = csv.excel
        reader = csv.DictReader(f, dialect=dialect)
        fields = reader.fieldnames or []
        email_col = next((c for c in fields if c and c.strip().lower() in ("email", "e-mail", "lead_email")), None) \
            or next((c for c in fields if c and "email" in c.lower()), None)
        if email_col is None:
            raise ValueError("Aucune colonne email trouvée dans le CSV")
        status_col = next((c for c in fields if c and c.strip().lower() in ("status", "statut", "lead_status")), None)
        out = []
        for row in reader:
            email = (row.get(email_col) or "").strip().lower()
            if not email or "@" not in email:
                continue
            if status_col and not any(w in (row.get(status_col) or "").lower() for w in BOUNCE_WORDS):
                continue
            out.append(email)
    return list(dict.fromkeys(out))


def apply_bounce(conn: sqlite3.Connection, email: str) -> int:
    rows = conn.execute("SELECT * FROM contacts WHERE valeur = ? AND type LIKE 'email_%'", (email,)).fetchall()
    ts = now()
    for c in rows:
        conn.execute("UPDATE contacts SET statut_verif = 'bounced', score = 0, verifie_le = ? WHERE id = ?",
                     (ts, c["id"]))
        if c["person_id"]:
            conn.execute("UPDATE persons SET email_statut = 'bounce' WHERE id = ?", (c["person_id"],))
        if c["source"] != "devine":
            continue
        domain = email.split("@", 1)[1]
        d = conn.execute("SELECT * FROM domains WHERE domaine = ?", (domain,)).fetchone()
        if d is None:
            continue
        if c["statut_verif"] == "valid":
            conn.execute("UPDATE domains SET smtp_fiable = 0 WHERE domaine = ?", (domain,))
            _downgrade(conn, domain, "statut_verif = 'valid'")
        if d["motif"] and c["motif"] == d["motif"] and d["motif_statut"] == "actif":
            bounces = d["motif_bounces"] + 1
            if bounces >= MOTIF_MAX_BOUNCES:
                conn.execute("UPDATE domains SET motif_bounces = ?, motif_statut = 'invalide' WHERE domaine = ?",
                             (bounces, domain))
                _downgrade(conn, domain, "motif = ? AND statut_verif != 'valid'", (d["motif"],))
            else:
                conn.execute("UPDATE domains SET motif_bounces = ? WHERE domaine = ?", (bounces, domain))
    return len(rows)


def _downgrade(conn: sqlite3.Connection, domain: str, where: str, params: tuple = ()) -> None:
    conn.execute(
        f"""UPDATE contacts SET score = MIN(score, ?)
            WHERE source = 'devine' AND type = 'email_dirigeant' AND valeur LIKE ?
              AND statut_verif NOT IN ('bounced', 'invalid') AND {where}""",
        (scoring.EMAIL_DEVINE, f"%@{domain}", *params),
    )


def run(conn: sqlite3.Connection, path: str | Path) -> dict:
    emails = read_bounces(path)
    matched = sum(1 for e in emails if apply_bounce(conn, e))
    conn.commit()
    return {"bounces_lus": len(emails), "trouves_en_base": matched}
