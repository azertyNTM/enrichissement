from enrichissement.names import detect_pattern, display_name, first_name_variants, generate_locals, last_name_variants


def test_variants_accents_and_compounds():
    assert first_name_variants("Jean-Pierre") == ["jean-pierre", "jeanpierre", "jean"]
    assert first_name_variants("Élodie") == ["elodie"]
    assert "delafontaine" in last_name_variants("de La Fontaine")
    assert "fontaine" in last_name_variants("de La Fontaine")
    assert "martin" in last_name_variants("Martin-Durand")


def test_detect_pattern():
    assert detect_pattern("jean-pierre.dupont", "Jean-Pierre", "Dupont") == "prenom.nom"
    assert detect_pattern("jdupont", "Jean", "Dupont") == "pnom"
    assert detect_pattern("j.dupont", "Jean", "Dupont") == "p.nom"
    assert detect_pattern("dupont.jean", "Jean", "Dupont") == "nom.prenom"
    assert detect_pattern("elodie", "Élodie", "Martin") == "prenom"
    assert detect_pattern("contact", "Jean", "Dupont") is None
    assert detect_pattern("m.durand", "Jean", "Dupont") is None


def test_generate_locals_preferred_first():
    locals_ = generate_locals("Jean", "Dupont", preferred="pnom")
    assert locals_[0] == ("jdupont", "pnom")
    assert ("jean.dupont", "prenom.nom") in locals_
    assert len({l for l, _ in locals_}) == len(locals_)


def test_display_name():
    assert display_name("JEAN-PIERRE") == "Jean-Pierre"
    assert display_name("DE LA FONTAINE") == "De La Fontaine"


def test_first_name_candidates():
    from enrichissement.names import first_name_candidates
    assert first_name_candidates("Jean", "Jean Pierre Marie") == ["Jean", "Jean-Pierre"]
    assert first_name_candidates("Élodie", "Élodie") == ["Élodie"]
