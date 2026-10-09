"""Barème de confiance unique. Toute la logique « est-ce fiable ? » passe par ici."""

from __future__ import annotations

REGISTRE = 100            # donnée officielle (registre)
DOMAINE_VALIDE = 100      # SIREN/SIRET/TVA trouvé sur le site
TELEPHONE_SITE = 90       # numéro affiché sur le site validé
TELEPHONE_MAPS = 80       # fiche Google Maps au même code postal et au nom du cabinet
EMAIL_SITE_DIRIGEANT = 95 # email nominatif publié sur le site et rattaché au dirigeant
EMAIL_SMTP_VALIDE = 90    # deviné puis accepté par SMTP sur un domaine non catch-all
EMAIL_CABINET = 80        # email générique publié (contact@, accueil@…)
EMAIL_CABINET_GRATUIT = 70  # email générique publié mais sur un webmail (orange.fr, gmail.com…)
EMAIL_NOMINATIF = 70      # email nominatif publié, non rattaché à un dirigeant connu
EMAIL_MOTIF_CONFIRME = 60 # deviné avec un motif observé sur le domaine, non vérifiable
EMAIL_DEVINE = 40         # deviné sans motif connu, non vérifiable (catch-all, SMTP indisponible)
REJETE = 0                # SMTP invalide ou bounce


def score_email_dirigeant(source: str, statut_verif: str, motif_confirme: bool) -> int:
    """Score d'un email de dirigeant.

    - source : 'site' (publié) ou 'devine' (généré)
    - statut_verif : non_verifie | valid | invalid | catch_all | unknown | bounced
    - motif_confirme : le motif utilisé a été observé sur ce domaine (site ou SMTP) et n'a pas été invalidé
    """
    if statut_verif in ("invalid", "bounced"):
        return REJETE
    if source == "site":
        return EMAIL_SITE_DIRIGEANT
    if statut_verif == "valid":
        return EMAIL_SMTP_VALIDE
    if motif_confirme:
        return EMAIL_MOTIF_CONFIRME
    return EMAIL_DEVINE
