import csv

from enrichissement import emails, export, feedback, gdpr, scoring
from enrichissement.db import upsert_contact
from tests.test_smtp_emails import FakeSMTP, resolver, verifier


def person_id(conn, prenom):
    return conn.execute("SELECT id FROM persons WHERE prenom_usuel = ?", (prenom,)).fetchone()[0]


def test_bounce_zeroes_email_and_marks_smtp_unreliable(company_conn, tmp_path):
    fake = FakeSMTP(accept={"jean.dupont@cabinet-dupont.fr", "elodie.martin@cabinet-dupont.fr"})
    emails.run(company_conn, verifier(fake), daily_cap=1000, resolver=resolver, sleep=lambda s: None)
    path = tmp_path / "bounces.csv"
    path.write_text("Email,Status\nJean.Dupont@cabinet-dupont.fr,Bounced\nautre@x.fr,Active\n", encoding="utf-8")
    assert feedback.run(company_conn, path) == {"bounces_lus": 1, "trouves_en_base": 1}
    rows = {r["valeur"]: r for r in company_conn.execute("SELECT * FROM contacts")}
    assert rows["jean.dupont@cabinet-dupont.fr"]["score"] == 0
    assert rows["jean.dupont@cabinet-dupont.fr"]["statut_verif"] == "bounced"
    # l'autre « valide » SMTP du domaine n'est plus digne de confiance
    assert rows["elodie.martin@cabinet-dupont.fr"]["score"] == scoring.EMAIL_DEVINE
    assert company_conn.execute("SELECT smtp_fiable FROM domains").fetchone()[0] == 0
    # un nouveau passage ne redevine pas l'email qui a rebondi
    emails.run(company_conn, None, resolver=resolver)
    jean = company_conn.execute(
        "SELECT valeur FROM contacts WHERE person_id = ? AND score > 0", (person_id(company_conn, "Jean"),)).fetchall()
    assert jean and all(r[0] != "jean.dupont@cabinet-dupont.fr" for r in jean)


def test_motif_invalidated_after_two_bounces(company_conn, tmp_path):
    company_conn.execute("INSERT INTO domains (domaine, mx_hosts, motif, motif_source) "
                         "VALUES ('cabinet-dupont.fr', '[\"mx\"]', 'prenom.nom', 'site')")
    for prenom, email in (("Jean", "jean.dupont@cabinet-dupont.fr"), ("Élodie", "elodie.martin@cabinet-dupont.fr")):
        upsert_contact(company_conn, siren="123456789", person_id=person_id(company_conn, prenom),
                       type="email_dirigeant", valeur=email, source="devine",
                       score=scoring.EMAIL_MOTIF_CONFIRME, motif="prenom.nom", statut_verif="catch_all")
    feedback.apply_bounce(company_conn, "jean.dupont@cabinet-dupont.fr")
    d = company_conn.execute("SELECT * FROM domains").fetchone()
    assert d["motif_statut"] == "actif" and d["motif_bounces"] == 1
    assert company_conn.execute("SELECT score FROM contacts WHERE valeur LIKE 'elodie%'").fetchone()[0] == 60
    company_conn.execute("INSERT INTO contacts (siren, person_id, type, valeur, source, score, motif, statut_verif, collecte_le) "
                         "VALUES ('123456789', 0, 'email_dirigeant', 'x.y@cabinet-dupont.fr', 'devine', 60, 'prenom.nom', 'catch_all', 'now')")
    feedback.apply_bounce(company_conn, "x.y@cabinet-dupont.fr")
    assert company_conn.execute("SELECT motif_statut FROM domains").fetchone()[0] == "invalide"
    assert company_conn.execute("SELECT score FROM contacts WHERE valeur LIKE 'elodie%'").fetchone()[0] == scoring.EMAIL_DEVINE


def read(path):
    with open(path, encoding="utf-8") as f:
        return list(csv.DictReader(f))


def test_export_threshold_and_optout(company_conn, tmp_path):
    jean, elodie = person_id(company_conn, "Jean"), person_id(company_conn, "Élodie")
    upsert_contact(company_conn, siren="123456789", person_id=jean, type="email_dirigeant",
                   valeur="jean.dupont@cabinet-dupont.fr", source="site", score=95, url_source="https://cabinet-dupont.fr/equipe")
    upsert_contact(company_conn, siren="123456789", person_id=elodie, type="email_dirigeant",
                   valeur="elodie.martin@cabinet-dupont.fr", source="devine", score=40, statut_verif="catch_all")
    upsert_contact(company_conn, siren="123456789", type="telephone", valeur="+33475123456", source="site", score=90)
    out = tmp_path / "leads.csv"
    assert export.write_csv(company_conn, out, min_score=85) == 1
    row = read(out)[0]
    assert row["email"] == "jean.dupont@cabinet-dupont.fr" and row["telephone"] == "+33475123456"
    assert row["linkedin_recherche"].startswith("https://www.linkedin.com/search/results/people/?keywords=Jean+Dupont")
    # --all : Élodie apparaît, mais sans email (sous le seuil)
    assert export.write_csv(company_conn, out, min_score=85, include_all=True) == 2
    assert [r["email"] for r in read(out) if r["prenom"] == "Élodie"] == [""]
    # opposition : plus jamais exporté
    gdpr.opt_out_email(company_conn, "Jean.Dupont@cabinet-dupont.fr")
    assert export.write_csv(company_conn, out, min_score=85) == 0


def test_purge_keeps_converted(company_conn):
    company_conn.execute("UPDATE companies SET collecte_le = '2020-01-01T00:00:00+00:00'")
    assert gdpr.purge(company_conn, years=3, dry_run=True) == 1
    gdpr.mark_converted(company_conn, "123456789")
    assert gdpr.purge(company_conn, years=3) == 0
    company_conn.execute("UPDATE companies SET converti = 0")
    assert gdpr.purge(company_conn, years=3) == 1
    assert company_conn.execute("SELECT COUNT(*) FROM persons").fetchone()[0] == 0
