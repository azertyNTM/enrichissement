import httpx
import respx

from enrichissement.fetch import Fetcher
from enrichissement.search import SearchResult
from enrichissement.website import candidate_sites, find_siren, legal_links, run


def test_find_siren_formats():
    assert find_siren("SIREN : 123 456 789", "123456789") == "siren"
    assert find_siren("RCS Romans 123.456.789", "123456789") == "siren"
    assert find_siren("SIRET 123 456 789 00012", "123456789") == "siret"
    assert find_siren("TVA intracommunautaire : FR 12 123456789", "123456789") == "tva"
    assert find_siren("SIREN 1234567890", "123456789") is None
    assert find_siren("SIREN 987 654 321", "123456789") is None


def test_candidate_sites_filters_directories():
    results = [SearchResult("https://www.pappers.fr/entreprise/x-123456789"),
               SearchResult("https://www.societe.com/societe/x.html"),
               SearchResult("https://www.cabinet-dupont.fr/contact"),
               SearchResult("https://cabinet-dupont.fr/equipe"),
               SearchResult("https://autre-cabinet.fr/")]
    assert candidate_sites(results) == ["https://www.cabinet-dupont.fr/", "https://autre-cabinet.fr/"]


def test_legal_links():
    html = '<a href="/mentions-legales">Mentions légales</a><a href="https://ailleurs.fr/legal">x</a>'
    assert legal_links(html, "https://cabinet.fr/") == ["https://cabinet.fr/mentions-legales"]


class FakeProvider:
    name = "fake"

    def __init__(self, urls):
        self.urls = urls

    def search(self, query, count=10):
        return [SearchResult(u) for u in self.urls]


HOME = '<html><body><a href="/mentions-legales">Mentions légales</a><a href="/contact">Contact</a></body></html>'


@respx.mock
def test_run_validates_only_matching_siren(company_conn):
    conn = company_conn
    conn.execute("UPDATE companies SET domaine_statut = 'a_chercher', domaine = NULL")
    respx.get(url__regex=r".*/robots\.txt").mock(return_value=httpx.Response(404))
    # premier candidat : un autre cabinet (SIREN différent) -> rejeté
    respx.get("https://mauvais-cabinet.fr/").mock(return_value=httpx.Response(200, html=HOME))
    respx.get("https://mauvais-cabinet.fr/mentions-legales").mock(
        return_value=httpx.Response(200, html="<p>SIREN 999 888 777</p>"))
    # second candidat : le bon
    respx.get("https://www.cabinet-dupont.fr/").mock(return_value=httpx.Response(200, html=HOME))
    respx.get("https://www.cabinet-dupont.fr/mentions-legales").mock(
        return_value=httpx.Response(200, html="<p>SARL au capital de 10 000 € - RCS Romans 123 456 789</p>"))
    provider = FakeProvider(["https://mauvais-cabinet.fr/", "https://www.cabinet-dupont.fr/"])
    with httpx.Client() as client:
        stats = run(conn, Fetcher(client, "test", min_interval=0), provider)
    assert stats == {"traites": 1, "valides": 1, "non_trouves": 0}
    row = conn.execute("SELECT * FROM companies").fetchone()
    assert row["domaine"] == "cabinet-dupont.fr"
    assert row["domaine_statut"] == "valide"
    assert row["domaine_preuve"] == "https://www.cabinet-dupont.fr/mentions-legales"


@respx.mock
def test_run_rejects_when_siren_absent(company_conn):
    conn = company_conn
    conn.execute("UPDATE companies SET domaine_statut = 'a_chercher', domaine = NULL")
    respx.get(url__regex=r".*/robots\.txt").mock(return_value=httpx.Response(404))
    respx.get("https://homonyme.fr/").mock(return_value=httpx.Response(200, html=HOME))
    respx.get("https://homonyme.fr/mentions-legales").mock(return_value=httpx.Response(200, html="<p>Rien</p>"))
    with httpx.Client() as client:
        stats = run(conn, Fetcher(client, "test", min_interval=0), FakeProvider(["https://homonyme.fr/"]))
    assert stats["non_trouves"] == 1
    assert conn.execute("SELECT domaine FROM companies").fetchone()[0] is None


@respx.mock
def test_robots_disallow_is_respected():
    respx.get("https://interdit.fr/robots.txt").mock(
        return_value=httpx.Response(200, text="User-agent: *\nDisallow: /"))
    page = respx.get("https://interdit.fr/").mock(return_value=httpx.Response(200, html="x"))
    with httpx.Client() as client:
        assert Fetcher(client, "test", min_interval=0).get("https://interdit.fr/") is None
    assert not page.called
