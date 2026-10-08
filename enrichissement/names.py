"""Normalisation des noms et génération / reconnaissance des motifs d'email."""

from __future__ import annotations

import re
import unicodedata

PARTICULES = {"de", "du", "des", "la", "le", "d", "van", "von", "der", "di", "da"}


def strip_accents(s: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFKD", s) if not unicodedata.combining(c))


def ascii_lower(s: str) -> str:
    s = strip_accents(s).lower()
    s = s.replace("’", "'")
    return re.sub(r"[^a-z' \-]", "", s).strip()


def first_name_variants(prenom: str) -> list[str]:
    """'Jean-Pierre' -> ['jean-pierre', 'jeanpierre', 'jean']."""
    p = ascii_lower(prenom).replace("'", "")
    if not p:
        return []
    parts = [x for x in re.split(r"[\s\-]+", p) if x]
    out = ["-".join(parts), "".join(parts), parts[0]]
    return list(dict.fromkeys(x for x in out if x))


def last_name_variants(nom: str) -> list[str]:
    """'de La Fontaine' -> ['delafontaine', 'de-la-fontaine', 'fontaine'] ; 'Martin-Durand' -> [... 'martin']."""
    n = ascii_lower(nom).replace("'", " ")
    if not n:
        return []
    parts = [x for x in re.split(r"[\s\-]+", n) if x]
    core = [x for x in parts if x not in PARTICULES] or parts
    out = ["".join(parts), "-".join(parts), "".join(core), "-".join(core)]
    if len(core) > 1:
        out.append(core[0])
    return list(dict.fromkeys(x for x in out if x))


# motif -> fonction(prenom, nom) -> partie locale
PATTERNS: dict[str, callable] = {
    "prenom.nom": lambda f, l: f"{f}.{l}",
    "p.nom": lambda f, l: f"{f[0]}.{l}",
    "prenom": lambda f, l: f,
    "nom": lambda f, l: l,
    "prenomnom": lambda f, l: f"{f}{l}",
    "pnom": lambda f, l: f"{f[0]}{l}",
    "nom.prenom": lambda f, l: f"{l}.{f}",
    "prenom-nom": lambda f, l: f"{f}-{l}",
    "prenom_nom": lambda f, l: f"{f}_{l}",
    "nomp": lambda f, l: f"{l}{f[0]}",
    "nom.p": lambda f, l: f"{l}.{f[0]}",
}

# ordre de probabilité par défaut pour les cabinets français
DEFAULT_ORDER = ["prenom.nom", "p.nom", "prenom", "pnom", "nom", "prenomnom",
                 "nom.prenom", "prenom-nom", "prenom_nom", "nomp", "nom.p"]


def first_name_candidates(prenom_usuel: str, prenoms: str) -> list[str]:
    """Le registre liste les prénoms séparés par des espaces ('Jean Pierre') : le prénom usuel
    peut être le premier ('Jean') ou un prénom composé ('Jean-Pierre')."""
    out = [prenom_usuel] if prenom_usuel else []
    parts = prenoms.split()
    if len(parts) >= 2:
        out.append(f"{parts[0]}-{parts[1]}")
    return out


def detect_pattern(local: str, prenom: str, nom: str) -> str | None:
    """Retourne le motif si la partie locale correspond à cette personne.

    Les motifs ambigus à une seule lettre (p.nom, pnom…) ne sont acceptés que si le nom complet est présent.
    'prenom' seul n'est accepté que si le prénom fait au moins 3 lettres.
    """
    local = local.lower()
    for pattern in DEFAULT_ORDER:
        fn = PATTERNS[pattern]
        for f in first_name_variants(prenom):
            for l in last_name_variants(nom):
                if pattern == "prenom" and len(f) < 3:
                    continue
                if pattern == "nom" and len(l) < 3:
                    continue
                if fn(f, l) == local:
                    return pattern
    return None


def generate_locals(prenom: str, nom: str, preferred: str | None = None) -> list[tuple[str, str]]:
    """Variantes (partie locale, motif) ordonnées : motif préféré d'abord, puis ordre par défaut.

    On n'utilise que la variante principale du prénom et du nom pour limiter le nombre de tests SMTP.
    """
    fv, lv = first_name_variants(prenom), last_name_variants(nom)
    if not fv or not lv:
        return []
    f, l = fv[0], lv[0]
    order = ([preferred] if preferred in PATTERNS else []) + [p for p in DEFAULT_ORDER if p != preferred]
    out: list[tuple[str, str]] = []
    seen: set[str] = set()
    for pattern in order:
        local = PATTERNS[pattern](f, l)
        if local not in seen:
            seen.add(local)
            out.append((local, pattern))
    return out


def display_name(s: str) -> str:
    """'DUPONT' -> 'Dupont', 'jean-pierre' -> 'Jean-Pierre'."""
    return re.sub(r"[^\s\-']+", lambda m: m.group(0).capitalize(), s.strip().lower())
