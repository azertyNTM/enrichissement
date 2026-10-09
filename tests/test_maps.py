from enrichissement import maps


def test_run(company_conn):
    conn = company_conn  # CABINET DUPONT EXPERTISE (CDE), 26000 Valence
    calls = []

    def places(q):
        calls.append(q)
        return [
            {"title": "Cabinet Dupont", "address": "1 rue X, 30000 Nîmes", "phoneNumber": "04 66 00 00 00"},  # autre agence
            {"title": "Boulangerie", "address": "2 rue Y, 26000 Valence", "phoneNumber": "04 75 11 11 11"},  # autre nom
            {"title": "C.D.E.", "address": "12 Rue des Alpes, 26000 Valence", "phoneNumber": "04 75 22 22 22"},
        ]

    assert maps.run(conn, places) == {"traites": 1, "trouves": 1, "non_trouves": 0}
    tel = conn.execute("SELECT valeur, source, score FROM contacts WHERE type = 'telephone'").fetchone()
    assert tuple(tel) == ("+33475222222", "google_maps", 80)
    assert "C.D.E., 12 Rue des Alpes" in conn.execute("SELECT url_source FROM contacts").fetchone()[0]
    assert maps.run(conn, places)["traites"] == 0  # reprenable


def test_matches_rejects_weak_names():
    def co(name, cp="26000"):
        return {"raison_sociale": name, "sigle": None, "code_postal": cp}

    def ok(name, title):
        return maps.matches({"title": title, "address": "1 rue, 26000 Valence"}, co(name))

    assert ok("ACCAL", "A.c.c.a.l.")
    assert ok("EUREX CRMD AUDIT", "Eurex Expert Comptable Valence")      # un mot + métier
    assert ok("FIDUCIAIRE DER BAGHDASSARIAN", "Der Baghdassarian Gabriel")  # tous les mots
    assert not ok("JLNP FINANCES", "Centre des Finances publiques")
    assert not ok("CAP CDBA", "CAP INFORMATIQUE 26")
    assert not ok("TJA CONSEIL", "TJ Arts Martiaux Academie")
    assert not ok("CHRISTOPHE ARNAUD", "Petit Arnaud")
