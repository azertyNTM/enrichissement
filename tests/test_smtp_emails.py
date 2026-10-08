import json

from enrichissement import emails, scoring
from enrichissement.smtp_verify import SmtpVerifier


class FakeSMTP:
    """Serveur SMTP simulé : `accept` = adresses acceptées, `catch_all` accepte tout, `grey` renvoie 450."""

    def __init__(self, accept=(), catch_all=False, grey=False):
        self.accept, self.catch_all, self.grey = set(accept), catch_all, grey
        self.rcpts = []

    def __call__(self, host, port, timeout=None, local_hostname=None):
        return self

    def ehlo_or_helo_if_needed(self):
        pass

    def mail(self, sender):
        return 250, b"ok"

    def rcpt(self, addr):
        self.rcpts.append(addr)
        if self.grey:
            return 450, b"greylisted"
        if self.catch_all or addr in self.accept:
            return 250, b"ok"
        return 550, b"no such user"

    def quit(self):
        pass


def verifier(fake):
    return SmtpVerifier("helo.test", "verif@test.fr", smtp_factory=fake)


def test_check_detects_catch_all():
    res = verifier(FakeSMTP(catch_all=True)).check("x.fr", ["mx.x.fr"], ["a@x.fr", "b@x.fr"])
    assert res.catch_all is True
    assert res.results == {"a@x.fr": "catch_all", "b@x.fr": "catch_all"}


def test_check_valid_invalid_and_greylisting():
    res = verifier(FakeSMTP(accept={"a@x.fr"})).check("x.fr", ["mx.x.fr"], ["a@x.fr", "b@x.fr"])
    assert res.catch_all is False
    assert res.results == {"a@x.fr": "valid", "b@x.fr": "invalid"}
    res = verifier(FakeSMTP(grey=True)).check("x.fr", ["mx.x.fr"], ["a@x.fr"])
    assert res.catch_all is None and res.results == {"a@x.fr": "unknown"}


def resolver(domain):
    return ["mx1.cabinet-dupont.fr"]


def best(conn, prenom):
    return conn.execute(
        """SELECT c.* FROM contacts c JOIN persons p ON p.id = c.person_id
           WHERE p.prenom_usuel = ? AND c.type = 'email_dirigeant' ORDER BY c.score DESC LIMIT 1""",
        (prenom,)).fetchone()


def test_emails_smtp_valid_gives_90_and_learns_pattern(company_conn):
    fake = FakeSMTP(accept={"jdupont@cabinet-dupont.fr", "emartin@cabinet-dupont.fr"})
    stats = emails.run(company_conn, verifier(fake), daily_cap=1000, resolver=resolver, sleep=lambda s: None)
    assert stats["verifies_smtp"] == 2
    row = best(company_conn, "Jean")
    assert row["valeur"] == "jdupont@cabinet-dupont.fr" and row["score"] == scoring.EMAIL_SMTP_VALIDE
    assert row["statut_verif"] == "valid"
    dom = company_conn.execute("SELECT * FROM domains").fetchone()
    assert dom["motif"] == "pnom" and dom["motif_source"] == "smtp" and dom["catch_all"] == 0
    assert json.loads(dom["mx_hosts"]) == ["mx1.cabinet-dupont.fr"]
    # Élodie : le motif appris est testé en premier, donc une seule tentative invalide avant le succès
    assert best(company_conn, "Élodie")["valeur"] == "emartin@cabinet-dupont.fr"


def test_emails_catch_all_is_not_declared_valid(company_conn):
    emails.run(company_conn, verifier(FakeSMTP(catch_all=True)), daily_cap=1000, resolver=resolver,
               sleep=lambda s: None)
    row = best(company_conn, "Jean")
    assert row["statut_verif"] == "catch_all"
    assert row["score"] == scoring.EMAIL_DEVINE
    assert company_conn.execute("SELECT catch_all FROM domains").fetchone()[0] == 1


def test_emails_catch_all_with_site_pattern_scores_60(company_conn):
    company_conn.execute("INSERT INTO domains (domaine, motif, motif_source) VALUES ('cabinet-dupont.fr', 'nom.prenom', 'site')")
    emails.run(company_conn, verifier(FakeSMTP(catch_all=True)), daily_cap=1000, resolver=resolver,
               sleep=lambda s: None)
    row = best(company_conn, "Jean")
    assert row["valeur"] == "dupont.jean@cabinet-dupont.fr"
    assert row["score"] == scoring.EMAIL_MOTIF_CONFIRME


def test_emails_without_smtp_stores_one_unverified_guess(company_conn):
    emails.run(company_conn, None, resolver=resolver)
    rows = company_conn.execute("SELECT * FROM contacts WHERE type = 'email_dirigeant'").fetchall()
    assert len(rows) == 2
    assert all(r["statut_verif"] == "non_verifie" and r["score"] == scoring.EMAIL_DEVINE for r in rows)


def test_emails_all_invalid(company_conn):
    emails.run(company_conn, verifier(FakeSMTP()), daily_cap=1000, resolver=resolver, sleep=lambda s: None)
    statuts = {r[0] for r in company_conn.execute(
        "SELECT email_statut FROM persons WHERE type_dirigeant = 'personne physique'")}
    assert statuts == {"aucun_valide"}
    assert company_conn.execute("SELECT MAX(score) FROM contacts").fetchone()[0] == 0


def test_emails_no_mx(company_conn):
    emails.run(company_conn, None, resolver=lambda d: [])
    statuts = {r[0] for r in company_conn.execute(
        "SELECT email_statut FROM persons WHERE type_dirigeant = 'personne physique'")}
    assert statuts == {"pas_de_mx"}


def test_daily_cap_disables_smtp(company_conn):
    fake = FakeSMTP(accept={"jdupont@cabinet-dupont.fr"})
    emails.run(company_conn, verifier(fake), daily_cap=3, resolver=resolver, sleep=lambda s: None)
    assert fake.rcpts == []
    assert best(company_conn, "Jean")["statut_verif"] == "non_verifie"
