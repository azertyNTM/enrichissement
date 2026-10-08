# Enrichissement de leads : cabinets d'expertise comptable

Pipeline d'enrichissement conçu pour la **fiabilité**. On part du registre officiel, et chaque donnée est stockée avec **sa source, sa date de collecte et un score de confiance**. Quand l'outil ne sait pas, il le dit au lieu d'inventer.

```
Registre officiel ──► Site web validé par SIREN ──► Téléphone / emails publiés ──► Email dirigeant (MX + SMTP) ──► Score ──► CSV
       (1)                     (2)                           (3)                            (4)                     (5)
                                                                      ▲
                                                Bounces Instantly ────┘ (boucle de retour)
```

## Installation

```bash
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
cp .env.example .env   # puis renseigner BRAVE_API_KEY ou SERPER_API_KEY
```

## Utilisation

```bash
enrich registry --dept 26            # 1. cabinets actifs NAF 69.20Z de la Drôme (API publique, sans clé)
enrich websites                      # 2. recherche du site + validation par SIREN (clé Brave ou Serper)
enrich scrape                        # 3. téléphone, emails publiés, motif d'email
enrich emails                        # 4. email des dirigeants (SMTP si SMTP_ENABLED=true ou --smtp)
enrich export leads.csv              # 5. export des dirigeants dont l'email a un score ≥ EXPORT_MIN_SCORE (85)
enrich run --dept 26 --limit 20      # tout d'un coup (sur un échantillon)
enrich stats                         # résumé de la base
```

Les étapes sont **reprenables** : relancer une commande ne retraite que ce qui manque (`enrich websites --retry` retente les cabinets « non trouvés »).

## Ce qui rend l'outil fiable

| Étape | Règle |
|---|---|
| 1. Registre | Source officielle `recherche-entreprises.api.gouv.fr` : SIREN, adresse, effectif, dirigeants. Seuls les établissements actifs sont importés. |
| 2. Site web | Un domaine n'est accepté **que si le SIREN, le SIRET ou le n° de TVA du cabinet figure sur ses mentions légales** (ou sa page d'accueil). Les annuaires (Pappers, Societe.com…) sont exclus. Sans preuve, le site reste « non trouvé ». La page qui sert de preuve est conservée (`domaine_preuve`). |
| 3. Extraction | Uniquement sur le site validé. Les emails de tiers (hébergeur, agence web) sont ignorés, ainsi que le téléphone de l'hébergeur et les numéros de fax. Un email nominatif n'est rattaché à un dirigeant que si son nom correspond. |
| 4. Email dirigeant | Les variantes sont générées en essayant d'abord le motif observé sur le domaine. **Test catch-all avec une adresse bidon** : si le serveur accepte tout, aucun email n'est déclaré « valide ». Une réponse 4xx (greylisting) donne « inconnu », jamais « invalide ». |
| 5. Score | Un barème unique, défini dans `enrichissement/scoring.py` (voir ci-dessous). Seuls les emails au-dessus du seuil sont exportés. |

### Barème

| Situation | Score |
|---|---|
| Email publié sur le site et rattaché au dirigeant | 95 |
| Email deviné **validé par SMTP** sur un domaine non catch-all | 90 |
| Téléphone affiché sur le site validé | 90 |
| Email générique publié (contact@…) | 80 |
| Email deviné, motif confirmé sur le domaine, non vérifiable (catch-all) | 60 |
| Email deviné sans motif, non vérifiable | 40 |
| SMTP invalide ou rebond | 0 |

## Vérification SMTP : à lire avant d'activer

1. **Port 25** : beaucoup d'hébergeurs le bloquent en sortie. Testez-le sur le VPS avec `enrich check-port25`.
2. **Réputation IP** : l'IP doit avoir un reverse DNS cohérent avec `SMTP_HELO_HOST`. L'outil fait une connexion par domaine, des pauses de 20 à 40 s entre domaines (`SMTP_PAUSE_MIN/MAX`) et respecte un plafond journalier (`SMTP_DAILY_CAP`).
3. Si le port 25 est fermé, laissez `SMTP_ENABLED=false`. Les emails devinés restent en base avec un score de 40 ou 60, et vous pouvez faire valider seulement ceux-là par un vérificateur payant à l'unité.

## Boucle de retour (bounces Instantly)

```bash
enrich feedback bounces.csv
```

Le CSV doit avoir une colonne `email`. S'il a aussi une colonne `status`, seules les lignes de rebond sont prises en compte. Ensuite :

- l'email qui a rebondi passe à 0 et ne sera plus jamais proposé ;
- si c'était un email « validé SMTP », le serveur du domaine n'est plus jugé fiable et ses autres emails validés retombent à 40 ;
- après 2 rebonds sur le motif d'un domaine, le motif est invalidé et les emails devinés avec ce motif retombent à 40 ;
- au prochain `enrich emails`, le dirigeant est retraité, sans réessayer l'adresse qui a rebondi.

## Ce que l'outil ne fait pas, volontairement

- **Pas de scraping LinkedIn.** L'export contient une colonne `linkedin_recherche` : un lien de recherche prêt à cliquer.
- **Pas d'achat de bases d'emails.**
- Scraping poli : user-agent identifiable (`USER_AGENT`), respect de `robots.txt`, une requête par seconde et par site.

## RGPD

- Chaque donnée garde sa **source** (`url_source`, `domaine_preuve`, `source_dirigeant`) et sa **date de collecte**.
- **Opposition** : `enrich optout email@cabinet.fr` ou `enrich optout --siren 123456789`. L'email est conservé haché (SHA-256) dans une liste repoussoir et n'est plus jamais exporté, même après un nouvel enrichissement.
- **Conservation** : `enrich purge` supprime les prospects non convertis collectés il y a plus de `RETENTION_YEARS` ans (3 par défaut, durée de référence de la CNIL pour la prospection). `enrich converti SIREN` exclut un client de la purge. `--dry-run` affiche ce qui serait supprimé sans rien effacer.
- En B2B, la prospection par email est permise si le message est en rapport avec la fonction de la personne, si elle est informée et si elle peut s'y opposer simplement (lien de désinscription dans chaque email).

## Structure

```
enrichissement/
  registry.py     étape 1 (API Recherche d'entreprises)
  search.py       étape 2a (Brave / Serper, choisi par SEARCH_PROVIDER)
  website.py      étape 2b (validation SIREN / SIRET / TVA)
  scrape.py       étape 3 (téléphone, emails, motif)
  names.py        normalisation des noms, génération et reconnaissance des motifs
  mx.py           MX et fournisseur mail
  smtp_verify.py  RCPT TO, détection catch-all, test du port 25
  emails.py       étape 4 (orchestration)
  scoring.py      étape 5 (barème)
  feedback.py     bounces Instantly
  export.py       CSV (Google Sheets / Instantly)
  gdpr.py         opposition, purge
  db.py           schéma SQLite
  cli.py          commandes `enrich`
```

## Tests

```bash
pytest
```

Les tests tournent hors ligne : les API, les sites et le serveur SMTP sont simulés. Ils couvrent notamment les SIREN espacés, SIRET et n° de TVA, les annuaires exclus, `robots.txt`, l'email et le téléphone de l'hébergeur ignorés, les prénoms composés, les domaines catch-all, le greylisting, le plafond SMTP, les bounces, l'opposition et la purge.
