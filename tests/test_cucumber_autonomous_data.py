from tools import cucumber_decisions as d


def _entry(tag="@agent_x_1_1", step="CONTENT- Le stock suivant", avant="ALS.L05.04.0.3", apres="ALS.L04.00.1.1",
           nature="validation_humaine"):
    return {"tag": tag, "nature": nature, "cause": "produit non implanté sur l'emplacement",
            "valeur": {"step": step, "avant": avant, "apres": apres}}


def test_changement_de_mise_en_place_applique(monkeypatch):
    monkeypatch.setenv("CUCUMBER_AUTONOMY", "1")
    out = d.data_changes_to_apply([_entry()], {"@agent_x_1_1"})
    assert list(out) == ["@agent_x_1_1"]


def test_attendu_jamais_applique():
    assert not d.is_setup_change(_entry(step="PREP- Contrôle des ordres de préparation"))
    assert not d.is_setup_change(_entry(step="KIS- Le process interactif affiche"))
    assert not d.is_setup_change(_entry(apres=""))


def test_autre_nature_ou_autre_scenario_ignores():
    assert d.data_changes_to_apply([_entry(nature="a_verifier")], {"@agent_x_1_1"}) == {}
    assert d.data_changes_to_apply([_entry(tag="@autre")], {"@agent_x_1_1"}) == {}


def test_consigne_d_application():
    lines = d.apply_instruction_lines([_entry()])
    assert "APPLIQUE" in lines[0]
    assert "ALS.L05.04.0.3" in lines[1] and "ALS.L04.00.1.1" in lines[1]


def test_regle_du_prompt_selon_l_autonomie(monkeypatch):
    monkeypatch.setenv("CUCUMBER_AUTONOMY", "1")
    assert "APPLIQUES" in d.data_change_rule()
    monkeypatch.setenv("CUCUMBER_AUTONOMY", "0")
    assert "décision humaine" in d.data_change_rule()
