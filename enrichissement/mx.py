"""Étape 4b : enregistrements MX et identification du fournisseur mail."""

from __future__ import annotations

import dns.exception
import dns.resolver

PROVIDERS = [
    ("protection.outlook.com", "microsoft365"),
    ("outlook.com", "microsoft365"),
    ("google.com", "google"),
    ("googlemail.com", "google"),
    ("ovh.net", "ovh"),
    ("mail.ovh", "ovh"),
    ("kundenserver.de", "ionos"),
    ("ionos", "ionos"),
    ("gandi.net", "gandi"),
    ("o2switch.net", "o2switch"),
    ("orange.fr", "orange"),
    ("mailinblack", "mailinblack"),
    ("mimecast", "mimecast"),
    ("pphosted.com", "proofpoint"),
    ("zoho", "zoho"),
    ("infomaniak", "infomaniak"),
]


def lookup_mx(domain: str, timeout: float = 5.0) -> list[str]:
    """Hôtes MX triés par préférence ; liste vide si aucun MX (le domaine ne reçoit pas d'email)."""
    try:
        answers = dns.resolver.resolve(domain, "MX", lifetime=timeout)
    except (dns.resolver.NXDOMAIN, dns.resolver.NoAnswer, dns.resolver.NoNameservers,
            dns.exception.Timeout):
        return []
    records = sorted((r.preference, str(r.exchange).rstrip(".").lower()) for r in answers)
    return [host for _, host in records if host]


def detect_provider(mx_hosts: list[str]) -> str:
    for host in mx_hosts:
        for needle, name in PROVIDERS:
            if needle in host:
                return name
    return "autre" if mx_hosts else "aucun"
