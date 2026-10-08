"""Étape 4b : vérification SMTP (RCPT TO) sans envoyer d'email, avec détection des catch-all.

Principes de prudence :
- on teste d'abord une adresse aléatoire : si elle est acceptée, le domaine est catch-all et
  aucun résultat « valide » n'a de sens -> statut 'catch_all' ;
- une réponse 4xx (greylisting, limitation) donne 'unknown', jamais 'invalid' ;
- une seule connexion par domaine, des pauses entre domaines, un plafond journalier.
"""

from __future__ import annotations

import secrets
import smtplib
import socket
from dataclasses import dataclass, field


@dataclass
class DomainCheck:
    catch_all: bool | None = None  # None = indéterminé
    results: dict[str, str] = field(default_factory=dict)  # email -> valid|invalid|catch_all|unknown
    codes: dict[str, int | None] = field(default_factory=dict)
    error: str | None = None


def classify(code: int | None) -> str:
    if code is None:
        return "unknown"
    if code in (250, 251):
        return "valid"
    if 500 <= code < 600:
        return "invalid"
    return "unknown"


class SmtpVerifier:
    def __init__(self, helo_host: str, mail_from: str, timeout: float = 15.0,
                 smtp_factory=smtplib.SMTP):
        self.helo_host = helo_host
        self.mail_from = mail_from
        self.timeout = timeout
        self.smtp_factory = smtp_factory

    def check(self, domain: str, mx_hosts: list[str], addresses: list[str]) -> DomainCheck:
        result = DomainCheck()
        if not mx_hosts:
            result.error = "pas de MX"
            return result
        probe = f"zz-{secrets.token_hex(6)}-nexistepas@{domain}"
        last_error = None
        for host in mx_hosts[:2]:
            try:
                smtp = self.smtp_factory(host, 25, timeout=self.timeout, local_hostname=self.helo_host)
            except (OSError, smtplib.SMTPException) as exc:
                last_error = f"{host}: {exc}"
                continue
            try:
                smtp.ehlo_or_helo_if_needed()
                code, _ = smtp.mail(self.mail_from)
                if code != 250:
                    result.error = f"MAIL FROM refusé ({code})"
                    return result
                probe_code, _ = smtp.rcpt(probe)
                result.codes[probe] = probe_code
                probe_status = classify(probe_code)
                if probe_status == "valid":
                    result.catch_all = True
                elif probe_status == "invalid":
                    result.catch_all = False
                for addr in addresses:
                    if result.catch_all:
                        result.results[addr] = "catch_all"
                        continue
                    code, _ = smtp.rcpt(addr)
                    result.codes[addr] = code
                    status = classify(code)
                    # sans réponse nette sur l'adresse bidon, un « valide » n'est pas fiable
                    if status == "valid" and result.catch_all is None:
                        status = "unknown"
                    result.results[addr] = status
                return result
            except (OSError, smtplib.SMTPException) as exc:
                last_error = f"{host}: {exc}"
                result = DomainCheck()
            finally:
                try:
                    smtp.quit()
                except Exception:  # noqa: BLE001
                    pass
        result.error = last_error or "MX injoignables"
        for addr in addresses:
            result.results.setdefault(addr, "unknown")
        return result


def check_port25(host: str = "gmail-smtp-in.l.google.com", timeout: float = 10.0) -> tuple[bool, str]:
    """Teste si le port 25 sortant est ouvert depuis cette machine."""
    try:
        with socket.create_connection((host, 25), timeout=timeout) as s:
            s.settimeout(timeout)
            banner = s.recv(512).decode(errors="replace").strip()
        return True, banner
    except OSError as exc:
        return False, str(exc)
