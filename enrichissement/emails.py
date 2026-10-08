"""Étape 4 : générer l'email des dirigeants, puis le vérifier (MX + SMTP) quand c'est possible."""

from __future__ import annotations

import json
import random
import sqlite3
import time
from datetime import datetime, timezone

from . import scoring
from .db import ensure_domain, get_domain, now, upsert_contact
from .mx import detect_provider, lookup_mx
from .names import generate_locals
from .smtp_verify import SmtpVerifier

MAX_CANDIDATES_SMTP = 6  # variantes testées par dirigeant (on évite de « bombarder » un serveur)


def motif_confirme(domain_row: sqlite3.Row | None, pattern: str) -> bool:
    return bool(domain_row and domain_row["motif"] == pattern and domain_row["motif_statut"] == "actif"
                and domain_row["motif_source"] in ("site", "smtp"))


def preferred_pattern(domain_row: sqlite3.Row | None) -> str | None:
    if domain_row and domain_row["motif_statut"] == "actif":
        return domain_row["motif"]
    return None


def ensure_mx(conn: sqlite3.Connection, domain: str, resolver=lookup_mx) -> sqlite3.Row:
    row = ensure_domain(conn, domain)
    if row["mx_hosts"] is None:
        hosts = resolver(domain)
        conn.execute("UPDATE domains SET mx_hosts = ?, mx_provider = ?, verifie_le = ? WHERE domaine = ?",
                     (json.dumps(hosts), detect_provider(hosts), now(), domain))
        row = get_domain(conn, domain)
    return row


def smtp_used_today(conn: sqlite3.Connection) -> int:
    today = datetime.now(timezone.utc).date().isoformat()
    return conn.execute("SELECT COUNT(*) FROM smtp_checks WHERE checked_at >= ?", (today,)).fetchone()[0]


def persons_to_process(conn: sqlite3.Connection, siren: str, smtp: bool) -> list[sqlite3.Row]:
    rows = conn.execute(
        """SELECT p.*, (SELECT MAX(score) FROM contacts c
                        WHERE c.person_id = p.id AND c.type = 'email_dirigeant') AS best
           FROM persons p
           WHERE p.siren = ? AND p.type_dirigeant = 'personne physique' AND p.opt_out = 0
             AND p.prenom_usuel != ''""",
        (siren,),
    ).fetchall()
    out = []
    for p in rows:
        best = p["best"]
        if best is not None and best >= scoring.EMAIL_SMTP_VALIDE:
            continue  # déjà fiable (publié ou vérifié)
        if not smtp and best is not None and best > 0:
            continue  # meilleure hypothèse déjà enregistrée, rien de plus à apprendre sans SMTP
        out.append(p)
    return out


def candidates_for(conn: sqlite3.Connection, person: sqlite3.Row, domain: str,
                   domain_row: sqlite3.Row | None) -> list[tuple[str, str]]:
    rejected = {r[0] for r in conn.execute(
        "SELECT valeur FROM contacts WHERE person_id = ? AND statut_verif IN ('invalid', 'bounced')",
        (person["id"],))}
    out = []
    for local, pattern in generate_locals(person["prenom_usuel"], person["nom"], preferred_pattern(domain_row)):
        email = f"{local}@{domain}"
        if email not in rejected:
            out.append((email, pattern))
    return out


def _store(conn, siren, person, email, pattern, statut, domain_row):
    score = scoring.score_email_dirigeant("devine", statut, motif_confirme(domain_row, pattern))
    upsert_contact(conn, siren=siren, person_id=person["id"], type="email_dirigeant", valeur=email,
                   source="devine", score=score, motif=pattern, statut_verif=statut)


def process_company(conn: sqlite3.Connection, company: sqlite3.Row, verifier: SmtpVerifier | None,
                    daily_cap: int, resolver=lookup_mx) -> dict:
    siren, domain = company["siren"], company["domaine"]
    stats = {"dirigeants": 0, "verifies": 0, "smtp": False}
    persons = persons_to_process(conn, siren, smtp=verifier is not None)
    if not persons:
        return stats
    drow = ensure_mx(conn, domain, resolver)
    mx_hosts = json.loads(drow["mx_hosts"] or "[]")
    if not mx_hosts:
        for p in persons:
            conn.execute("UPDATE persons SET email_statut = 'pas_de_mx' WHERE id = ?", (p["id"],))
        return stats

    per_person = {p["id"]: candidates_for(conn, p, domain, drow) for p in persons}
    use_smtp = (verifier is not None and drow["catch_all"] != 1 and drow["smtp_fiable"] == 1)
    to_test = [e for p in persons for e, _ in per_person[p["id"]][:MAX_CANDIDATES_SMTP]]
    if use_smtp and smtp_used_today(conn) + len(to_test) + 1 > daily_cap:
        use_smtp = False

    results: dict[str, str] = {}
    if use_smtp and to_test:
        check = verifier.check(domain, mx_hosts, to_test)
        stats["smtp"] = True
        ts = now()
        for addr, code in check.codes.items():
            conn.execute("INSERT INTO smtp_checks (domaine, adresse, code, resultat, checked_at) VALUES (?,?,?,?,?)",
                         (domain, addr, code, check.results.get(addr, "sonde"), ts))
        if check.catch_all is not None:
            conn.execute("UPDATE domains SET catch_all = ?, verifie_le = ? WHERE domaine = ?",
                         (int(check.catch_all), ts, domain))
        results = check.results
        drow = get_domain(conn, domain)

    for p in persons:
        cands = per_person[p["id"]]
        stats["dirigeants"] += 1
        if not cands:
            conn.execute("UPDATE persons SET email_statut = 'aucun_valide' WHERE id = ?", (p["id"],))
            continue
        tested = [(e, pat, results[e]) for e, pat in cands if e in results]
        valid = [(e, pat) for e, pat, st in tested if st == "valid"]
        for e, pat, st in tested:
            if st == "invalid":
                _store(conn, siren, p, e, pat, "invalid", drow)
        if valid:
            email, pattern = valid[0]
            _store(conn, siren, p, email, pattern, "valid", drow)
            stats["verifies"] += 1
            if drow["motif"] is None or drow["motif_statut"] == "invalide":
                conn.execute("UPDATE domains SET motif = ?, motif_source = 'smtp', motif_statut = 'actif', "
                             "motif_bounces = 0 WHERE domaine = ?", (pattern, domain))
                drow = get_domain(conn, domain)
            statut_p = "devine"
        elif tested and all(st == "invalid" for _, _, st in tested) and len(tested) == len(cands[:MAX_CANDIDATES_SMTP]):
            statut_p = "aucun_valide"
        else:
            # meilleure hypothèse, explicitement marquée non vérifiable
            remaining = [(e, pat) for e, pat in cands if results.get(e) != "invalid"]
            if not remaining:
                statut_p = "aucun_valide"
            else:
                email, pattern = remaining[0]
                if email in results:
                    statut = results[email]
                elif drow["catch_all"] == 1:
                    statut = "catch_all"
                else:
                    statut = "non_verifie"
                _store(conn, siren, p, email, pattern, statut, drow)
                statut_p = "devine"
        conn.execute("UPDATE persons SET email_statut = ? WHERE id = ?", (statut_p, p["id"]))
    return stats


def run(conn: sqlite3.Connection, verifier: SmtpVerifier | None, daily_cap: int = 150,
        pause: tuple[float, float] = (20.0, 40.0), limit: int | None = None,
        resolver=lookup_mx, sleep=time.sleep) -> dict:
    rows = conn.execute(
        "SELECT * FROM companies WHERE opt_out = 0 AND domaine_statut = 'valide' ORDER BY siren"
        + (" LIMIT ?" if limit else ""), (limit,) if limit else (),
    ).fetchall()
    total = {"cabinets": 0, "dirigeants": 0, "verifies_smtp": 0}
    for i, company in enumerate(rows):
        s = process_company(conn, company, verifier, daily_cap, resolver)
        conn.commit()
        total["cabinets"] += 1
        total["dirigeants"] += s["dirigeants"]
        total["verifies_smtp"] += s["verifies"]
        if s["smtp"] and i < len(rows) - 1:
            sleep(random.uniform(*pause))
    return total
