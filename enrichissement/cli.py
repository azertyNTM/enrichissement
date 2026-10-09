"""Interface en ligne de commande : `enrich --help`."""

from __future__ import annotations

import json
from typing import Optional

import httpx
import typer

from . import emails, export, feedback, gdpr, linkedin, maps, registry, scrape, website
from .config import Settings
from .db import connect, now
from .fetch import Fetcher, make_client
from .search import make_provider, serper_places
from .smtp_verify import SmtpVerifier, check_port25

app = typer.Typer(help="Enrichissement fiable de leads (cabinets d'expertise comptable, NAF 69.20Z).",
                  no_args_is_help=True)


def _ctx():
    s = Settings.from_env()
    return s, connect(s.db_path)


def _log(conn, etape: str, stats: dict, debut: str) -> None:
    conn.execute("INSERT INTO runs (etape, debut, fin, stats) VALUES (?, ?, ?, ?)",
                 (etape, debut, now(), json.dumps(stats, ensure_ascii=False)))
    conn.commit()
    typer.echo(f"[{etape}] " + ", ".join(f"{k}={v}" for k, v in stats.items()))


def _do_registry(s, conn, dept, naf, limit):
    debut = now()
    with make_client(s.http_timeout) as client:
        try:
            n = registry.import_registry(conn, client, dept, naf=naf, limit=limit)
        except (httpx.HTTPError, RuntimeError) as exc:
            conn.commit()
            typer.echo(f"API Recherche d'entreprises injoignable : {exc}", err=True)
            raise typer.Exit(1)
    _log(conn, "registre", {"departement": dept, "cabinets": n}, debut)


def _do_websites(s, conn, limit, retry):
    debut = now()
    with make_client(s.http_timeout) as client:
        try:
            provider = make_provider(s.search_provider, client, s.brave_api_key, s.serper_api_key)
        except ValueError as exc:
            typer.echo(f"Configuration : {exc} (voir .env.example)", err=True)
            raise typer.Exit(2)
        fetcher = Fetcher(client, s.user_agent, s.http_min_interval)
        try:
            stats = website.run(conn, fetcher, provider, limit=limit, retry=retry)
        except httpx.HTTPStatusError as exc:
            typer.echo(f"Recherche web refusée ({exc.response.status_code}) : {exc.response.text[:200]}", err=True)
            raise typer.Exit(1)
        except httpx.TransportError as exc:
            typer.echo(f"Recherche web injoignable ({exc!r}) : relancez, le travail fait est conservé", err=True)
            raise typer.Exit(1)
    _log(conn, "sites", stats, debut)


def _do_linkedin(s, conn, limit, retry):
    debut = now()
    with make_client(s.http_timeout) as client:
        try:
            provider = make_provider(s.search_provider, client, s.brave_api_key, s.serper_api_key)
        except ValueError as exc:
            typer.echo(f"Configuration : {exc} (voir .env.example)", err=True)
            raise typer.Exit(2)
        try:
            stats = linkedin.run(conn, provider, limit=limit, retry=retry)
        except httpx.HTTPStatusError as exc:
            typer.echo(f"Recherche web refusée ({exc.response.status_code}) : {exc.response.text[:200]}", err=True)
            raise typer.Exit(1)
        except httpx.TransportError as exc:
            typer.echo(f"Recherche web injoignable ({exc!r}) : relancez, le travail fait est conservé", err=True)
            raise typer.Exit(1)
    _log(conn, "linkedin", stats, debut)


def _do_scrape(s, conn, limit):
    debut = now()
    with make_client(s.http_timeout) as client:
        stats = scrape.run(conn, Fetcher(client, s.user_agent, s.http_min_interval), limit=limit)
    _log(conn, "extraction", stats, debut)


def _do_maps(s, conn, limit):
    if not s.serper_api_key:
        typer.echo("Configuration : SERPER_API_KEY manquant (Google Maps passe par Serper)", err=True)
        raise typer.Exit(2)
    debut = now()
    with make_client(s.http_timeout) as client:
        try:
            stats = maps.run(conn, lambda q: serper_places(client, s.serper_api_key, q), limit=limit)
        except httpx.HTTPError as exc:
            typer.echo(f"Google Maps (Serper) en erreur : {exc!r} : relancez, le travail fait est conservé", err=True)
            raise typer.Exit(1)
    _log(conn, "standard_maps", stats, debut)


def _do_emails(s, conn, smtp: bool, limit):
    debut = now()
    verifier = SmtpVerifier(s.smtp_helo_host, s.smtp_mail_from, s.smtp_timeout) if smtp else None
    stats = emails.run(conn, verifier, daily_cap=s.smtp_daily_cap,
                       pause=(s.smtp_pause_min, s.smtp_pause_max), limit=limit)
    stats["smtp"] = smtp
    _log(conn, "emails", stats, debut)


@app.command("registry")
def cmd_registry(dept: str = typer.Option(..., "--dept", help="Département, ex. 26"),
                 naf: str = typer.Option("69.20Z", help="Code NAF"),
                 limit: Optional[int] = typer.Option(None, help="Nombre max de cabinets")):
    """Étape 1 : importe les cabinets actifs depuis le registre officiel."""
    s, conn = _ctx()
    _do_registry(s, conn, dept, naf, limit)


@app.command("websites")
def cmd_websites(limit: Optional[int] = None,
                 retry: bool = typer.Option(False, help="Retenter les cabinets « non trouvés »")):
    """Étape 2 : trouve le site de chaque cabinet et le valide par le SIREN."""
    s, conn = _ctx()
    _do_websites(s, conn, limit, retry)


@app.command("linkedin")
def cmd_linkedin(limit: Optional[int] = None,
                 retry: bool = typer.Option(False, help="Retenter les dirigeants « non trouvés »")):
    """Étape 2c : trouve le profil LinkedIn de chaque dirigeant (nom + cabinet vérifiés)."""
    s, conn = _ctx()
    _do_linkedin(s, conn, limit, retry)


@app.command("scrape")
def cmd_scrape(limit: Optional[int] = None):
    """Étape 3 : extrait téléphone et emails publiés sur les sites validés."""
    s, conn = _ctx()
    _do_scrape(s, conn, limit)


@app.command("standard")
def cmd_standard(limit: Optional[int] = None):
    """Étape 3b : standard du cabinet via Google Maps (même code postal + même nom), si le site n'a rien donné."""
    s, conn = _ctx()
    _do_maps(s, conn, limit)


@app.command("emails")
def cmd_emails(smtp: Optional[bool] = typer.Option(None, "--smtp/--no-smtp",
                                                    help="Forcer/désactiver la vérification SMTP (défaut : SMTP_ENABLED)"),
               limit: Optional[int] = None):
    """Étape 4 : génère et vérifie l'email des dirigeants."""
    s, conn = _ctx()
    _do_emails(s, conn, s.smtp_enabled if smtp is None else smtp, limit)


@app.command("run")
def cmd_run(dept: str = typer.Option(..., "--dept"), naf: str = "69.20Z",
            limit: Optional[int] = None,
            smtp: Optional[bool] = typer.Option(None, "--smtp/--no-smtp")):
    """Toutes les étapes à la suite (reprenables : seules les données manquantes sont traitées)."""
    s, conn = _ctx()
    _do_registry(s, conn, dept, naf, limit)
    _do_websites(s, conn, limit, False)
    _do_linkedin(s, conn, limit, False)
    _do_scrape(s, conn, limit)
    _do_maps(s, conn, limit)
    _do_emails(s, conn, s.smtp_enabled if smtp is None else smtp, limit)


@app.command("export")
def cmd_export(path: str = typer.Argument("leads.csv"),
               min_score: Optional[int] = typer.Option(None, help="Seuil (défaut : EXPORT_MIN_SCORE)"),
               all_rows: bool = typer.Option(False, "--all", help="Inclure les dirigeants sans email fiable (email vide)")):
    """Exporte les leads au-dessus du seuil de confiance en CSV."""
    s, conn = _ctx()
    n = export.write_csv(conn, path, s.export_min_score if min_score is None else min_score, all_rows)
    typer.echo(f"{n} lignes écrites dans {path}")


@app.command("feedback")
def cmd_feedback(path: str):
    """Réinjecte les rebonds exportés d'Instantly (CSV)."""
    s, conn = _ctx()
    _log(conn, "feedback", feedback.run(conn, path), now())


@app.command("optout")
def cmd_optout(email: Optional[str] = typer.Argument(None), siren: Optional[str] = typer.Option(None)):
    """Opposition : l'email (ou tout le cabinet) ne sera plus jamais exporté."""
    s, conn = _ctx()
    if siren:
        typer.echo(f"Cabinet {siren} : {gdpr.opt_out_siren(conn, siren)} mis en opposition")
    if email:
        typer.echo(f"{email} ajouté à la liste d'opposition ({gdpr.opt_out_email(conn, email)} dirigeant(s) bloqué(s))")
    if not siren and not email:
        raise typer.BadParameter("Indiquez un email ou --siren")


@app.command("converti")
def cmd_converti(siren: str):
    """Marque un cabinet comme client (exclu de la purge)."""
    s, conn = _ctx()
    typer.echo(f"{gdpr.mark_converted(conn, siren)} cabinet(s) marqué(s) converti(s)")


@app.command("purge")
def cmd_purge(years: Optional[int] = typer.Option(None, help="Défaut : RETENTION_YEARS"),
              dry_run: bool = typer.Option(False, "--dry-run")):
    """Supprime les prospects non convertis plus anciens que la durée de conservation."""
    s, conn = _ctx()
    n = gdpr.purge(conn, s.retention_years if years is None else years, dry_run)
    typer.echo(f"{n} cabinet(s) {'à supprimer' if dry_run else 'supprimé(s)'}")


@app.command("stats")
def cmd_stats():
    """Résumé de la base."""
    s, conn = _ctx()
    q = lambda sql: conn.execute(sql).fetchone()[0]  # noqa: E731
    typer.echo(f"Cabinets : {q('SELECT COUNT(*) FROM companies')}")
    for statut, n in conn.execute("SELECT domaine_statut, COUNT(*) FROM companies GROUP BY 1"):
        typer.echo(f"  site {statut} : {n}")
    n_pers = q("SELECT COUNT(*) FROM persons WHERE principal = 1")
    n_tel = q("SELECT COUNT(DISTINCT siren) FROM contacts WHERE type = 'telephone'")
    typer.echo(f"Dirigeants principaux : {n_pers}")
    typer.echo(f"Cabinets avec téléphone : {n_tel}")
    n_li = q("SELECT COUNT(*) FROM persons WHERE linkedin_statut = 'trouve'")
    typer.echo(f"Profils LinkedIn trouvés : {n_li}")
    for label, lo in (("≥ 90", 90), ("≥ 60", 60), ("≥ 40", 40)):
        n = q(f"SELECT COUNT(DISTINCT person_id) FROM contacts WHERE type='email_dirigeant' AND score >= {lo}")
        typer.echo(f"Emails dirigeants {label} : {n}")


@app.command("check-port25")
def cmd_check_port25(host: str = "gmail-smtp-in.l.google.com"):
    """Vérifie que le port 25 sortant est ouvert (prérequis à la vérification SMTP)."""
    ok, msg = check_port25(host)
    typer.echo(("OUVERT : " if ok else "BLOQUÉ : ") + msg)
    raise typer.Exit(0 if ok else 1)


if __name__ == "__main__":
    app()
