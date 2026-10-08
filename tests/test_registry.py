import httpx
import respx

from enrichissement.registry import API_URL, import_registry, parse_company
from tests.conftest import RAW_COMPANY


def test_parse_company():
    company, persons = parse_company(RAW_COMPANY)
    assert company["siren"] == "123456789"
    assert company["raison_sociale"] == "CABINET DUPONT EXPERTISE"
    assert company["commune"] == "VALENCE"
    assert company["effectif_label"] == "10 à 19"
    physiques = [p for p in persons if p["type_dirigeant"] == "personne physique"]
    assert physiques[0]["prenom_usuel"] == "Jean"
    assert physiques[0]["nom"] == "Dupont"
    assert physiques[1]["prenom_usuel"] == "Élodie"
    assert any(p["type_dirigeant"] == "personne morale" for p in persons)


@respx.mock
def test_import_registry_paginates_and_is_idempotent(conn):
    second = dict(RAW_COMPANY, siren="222222222", dirigeants=[])
    route = respx.get(API_URL).mock(side_effect=[
        httpx.Response(200, json={"results": [RAW_COMPANY], "total_pages": 2}),
        httpx.Response(429),
        httpx.Response(200, json={"results": [second], "total_pages": 2}),
        httpx.Response(200, json={"results": [RAW_COMPANY], "total_pages": 1}),
    ])
    with httpx.Client() as client:
        assert import_registry(conn, client, "26") == 2
        assert route.calls[0].request.url.params["activite_principale"] == "69.20Z"
        assert route.calls[0].request.url.params["departement"] == "26"
        assert import_registry(conn, client, "26") == 1
    assert conn.execute("SELECT COUNT(*) FROM companies").fetchone()[0] == 2
    assert conn.execute("SELECT COUNT(*) FROM persons WHERE siren = '123456789'").fetchone()[0] == 3
