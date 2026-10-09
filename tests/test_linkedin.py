from enrichissement.linkedin import clean_profile_url, is_profile_url, run, score_result
from enrichissement.search import SearchResult


def test_profile_url():
    assert is_profile_url("https://fr.linkedin.com/in/jean-dupont-12ab/")
    assert not is_profile_url("https://www.linkedin.com/company/cabinet-dupont")
    assert clean_profile_url("https://fr.linkedin.com/in/jean-dupont-12ab/?trk=x") == \
        "https://www.linkedin.com/in/jean-dupont-12ab"


def test_score_result():
    tokens = {"dupont", "cde"}
    # nom + sigle du cabinet -> 90
    assert score_result("Jean Dupont - Gérant - CDE | LinkedIn", "", "Jean", "Dupont", tokens, "Valence") == 90
    # nom de famille dans la raison sociale ne compte pas comme preuve ; métier seul -> 70
    assert score_result("Jean Dupont - Expert-comptable | LinkedIn", "", "Jean", "Dupont", {"dupont"}, "") == 70
    # homonyme sans lien avec le cabinet ni le métier -> 0
    assert score_result("Jean Dupont - Boulanger | LinkedIn", "Lyon", "Jean", "Dupont", tokens, "Valence") == 0
    # mauvais prénom -> 0
    assert score_result("Paul Dupont - CDE | LinkedIn", "", "Jean", "Dupont", tokens, "") == 0
    # accents et prénoms composés
    assert score_result("Élodie Martin – Associée chez CDE", "", "Elodie", "MARTIN", tokens, "") == 90


class FakeProvider:
    name = "fake"

    def __init__(self, results):
        self.results, self.queries = results, []

    def search(self, query, count=10):
        self.queries.append(query)
        return self.results


def test_run(company_conn):
    conn = company_conn
    provider = FakeProvider([
        SearchResult("https://www.linkedin.com/company/cde"),
        SearchResult("https://fr.linkedin.com/in/jean-dupont-cde/", "Jean Dupont - Gérant - CDE | LinkedIn"),
    ])
    stats = run(conn, provider)
    assert stats == {"traites": 2, "trouves": 1, "non_trouves": 1}  # Dupont et Martin, co-gérants
    jean = conn.execute("SELECT * FROM persons WHERE nom = 'Dupont'").fetchone()
    assert (jean["linkedin_url"], jean["linkedin_score"]) == ("https://www.linkedin.com/in/jean-dupont-cde", 90)
    # reprenable : rien à refaire au second passage
    assert run(conn, provider)["traites"] == 0


def test_api_error_does_not_mark_not_found(company_conn):
    import httpx
    import pytest

    class Broken:
        def search(self, query, count=10):
            raise httpx.HTTPStatusError("400", request=httpx.Request("POST", "https://x"),
                                        response=httpx.Response(400, text="Not enough credits"))

    with pytest.raises(httpx.HTTPStatusError):
        run(company_conn, Broken())
    assert company_conn.execute("SELECT COUNT(*) FROM persons WHERE linkedin_statut = 'non_trouve'").fetchone()[0] == 0


def test_name_in_company_name_is_not_proof():
    # « GILLES DEVES » : ni le prénom ni le nom ne prouvent le lien avec le cabinet
    tokens = {"gilles", "deves"}
    assert score_result("Gilles Deves - Directeur de site", "", "Gilles", "Deves", tokens, "Valence") == 0
