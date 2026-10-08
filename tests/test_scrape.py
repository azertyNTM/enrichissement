import httpx
import respx

from enrichissement.fetch import Fetcher
from enrichissement.scrape import extract_emails, extract_phones, is_generic, run


def test_extract_emails():
    html = """<a href="mailto:Contact@Cabinet-Dupont.fr?subject=x">Écrire</a>
              <p>jean-pierre.dupont@cabinet-dupont.fr</p>
              <p>e.martin [at] cabinet-dupont [dot] fr</p>
              <img src="logo@2x.png">"""
    assert extract_emails(html) == {"contact@cabinet-dupont.fr", "jean-pierre.dupont@cabinet-dupont.fr",
                                    "e.martin@cabinet-dupont.fr"}


def test_extract_phones_ignores_fax_and_orders_fixed_first():
    text = "Mobile : 06 12 34 56 78 - Tél. 04.75.12.34.56 - Fax : 04 75 99 99 99 - +33 (0)4 75 00 00 01"
    assert extract_phones(text) == ["+33475123456", "+33475000001", "+33612345678"]


def test_is_generic():
    assert is_generic("contact")
    assert is_generic("accueil.valence")
    assert not is_generic("jean.dupont")


HOME = """<html><body><a href="/contact">Contact</a><a href="/notre-equipe">Notre équipe</a>
<a href="/mentions-legales">Mentions légales</a></body></html>"""
CONTACT = "<p>Standard : 04 75 12 34 56</p><a href='mailto:contact@cabinet-dupont.fr'>contact</a>"
TEAM = """<p>Jean-Pierre Dupont, gérant : jean-pierre.dupont@cabinet-dupont.fr</p>
<p>Lucie Bernard : lucie.bernard@cabinet-dupont.fr</p>"""
LEGAL = """<p>Cabinet Dupont, SIREN 123 456 789. Directeur de publication : J.-P. Dupont.</p>
<p>Hébergeur : OVH, 2 rue Kellermann, Roubaix - 09 72 10 10 07 - support@ovh.com</p>"""


@respx.mock
def test_scrape_company(company_conn):
    conn = company_conn
    respx.get(url__regex=r".*/robots\.txt").mock(return_value=httpx.Response(404))
    respx.get("https://cabinet-dupont.fr/").mock(return_value=httpx.Response(200, html=HOME))
    respx.get("https://cabinet-dupont.fr/contact").mock(return_value=httpx.Response(200, html=CONTACT))
    respx.get("https://cabinet-dupont.fr/notre-equipe").mock(return_value=httpx.Response(200, html=TEAM))
    respx.get("https://cabinet-dupont.fr/mentions-legales").mock(return_value=httpx.Response(200, html=LEGAL))
    with httpx.Client() as client:
        stats = run(conn, Fetcher(client, "test", min_interval=0))
    assert stats["traites"] == 1
    contacts = {(r["type"], r["valeur"]): r for r in conn.execute("SELECT * FROM contacts")}
    assert contacts[("telephone", "+33475123456")]["score"] == 90
    assert ("telephone", "+33972101007") not in contacts  # téléphone de l'hébergeur ignoré
    assert contacts[("email_cabinet", "contact@cabinet-dupont.fr")]["score"] == 80
    d = contacts[("email_dirigeant", "jean-pierre.dupont@cabinet-dupont.fr")]
    assert d["score"] == 95 and d["source"] == "site" and d["motif"] == "prenom.nom"
    prenom = conn.execute("SELECT prenom_usuel FROM persons WHERE nom = 'Dupont'").fetchone()[0]
    assert prenom == "Jean-Pierre"
    assert contacts[("email_nominatif", "lucie.bernard@cabinet-dupont.fr")]["score"] == 70
    assert not any(v.endswith("ovh.com") for _, v in contacts)
    dom = conn.execute("SELECT * FROM domains WHERE domaine = 'cabinet-dupont.fr'").fetchone()
    assert dom["motif"] == "prenom.nom" and dom["motif_source"] == "site"
