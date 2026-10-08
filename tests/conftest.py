import pytest

from enrichissement.db import connect
from enrichissement.registry import parse_company, save_company

RAW_COMPANY = {
    "siren": "123456789",
    "nom_complet": "CABINET DUPONT EXPERTISE (CDE)",
    "nom_raison_sociale": "CABINET DUPONT EXPERTISE",
    "sigle": "CDE",
    "activite_principale": "69.20Z",
    "tranche_effectif_salarie": "11",
    "date_creation": "2005-03-01",
    "siege": {"adresse": "12 RUE DES ALPES 26000 VALENCE", "code_postal": "26000",
              "libelle_commune": "VALENCE", "departement": "26", "siret": "12345678900012"},
    "dirigeants": [
        {"nom": "DUPONT", "prenoms": "JEAN PIERRE", "qualite": "Gérant", "type_dirigeant": "personne physique"},
        {"nom": "MARTIN", "prenoms": "Élodie", "qualite": "Associé", "type_dirigeant": "personne physique"},
        {"siren": "987654321", "denomination": "HOLDING DUPONT", "qualite": "Associé",
         "type_dirigeant": "personne morale"},
    ],
}


@pytest.fixture
def conn(tmp_path):
    c = connect(str(tmp_path / "test.db"))
    yield c
    c.close()


@pytest.fixture
def company_conn(conn):
    company, persons = parse_company(RAW_COMPANY)
    save_company(conn, company, persons)
    conn.execute("""UPDATE companies SET domaine = 'cabinet-dupont.fr', domaine_url = 'https://cabinet-dupont.fr/',
                    domaine_statut = 'valide', domaine_preuve = 'https://cabinet-dupont.fr/mentions-legales',
                    domaine_match = 'siren' WHERE siren = '123456789'""")
    conn.commit()
    return conn
