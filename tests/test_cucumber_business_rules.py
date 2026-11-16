"""Règles métier du client dans la conception des tests (axe B) : client fictif, aucune base réelle."""
import json

from tools import cucumber_business_rules as br

UP = br.ENTITIES[0][1]
NO_NEED, PALLETIZE_PROFILE, SORT_PROFILE = "p-none", "p-pal", "p-sort"
RULE = {"keyName": "x/rule-assign-need", "userRule": {"expressions": [
    {"condition": "wmsEnumNeed == $WmsEnumNeed.SORT() && (wmsPreparationUnit.mechanization == "
                  "$WmsEnumMechanization.MECHANIZED()||wmsPreparationUnit.mechanization == $WmsEnumMechanization.MIXED())",
     "conditionDescription": "Tri si mécanisé", "value": SORT_PROFILE},
    {"condition": "wmsEnumNeed == $WmsEnumNeed.PALLETIZE() && wmsPreparationUnit.mechanization == "
                  "$WmsEnumMechanization.UNMECHANIZED() && !wmsPreparationUnit.refPackaging.code.equals(\"CO\")",
     "conditionDescription": "Pas de palettisation si non mécanisé", "value": NO_NEED},
    {"condition": "wmsEnumNeed == $WmsEnumNeed.PALLETIZE() && wmsPreparationUnit.lines.any{it.x == null}",
     "conditionDescription": "complexe", "value": NO_NEED, "disabled": True},
    {"condition": "wmsEnumNeed == $WmsEnumNeed.PALLETIZE() ", "conditionDescription": "Par défaut", "value":
        PALLETIZE_PROFILE}]},
    "defaultRule": {"expressions": [
        {"condition": "wmsEnumNeed == $WmsEnumNeed.PALLETIZE() && wmsPreparationUnit.wmsPreparationOrder?.mode == "
                      "$Mode.PALLET()", "conditionDescription": "Standard", "value": PALLETIZE_PROFILE}],
        "otherwise": NO_NEED}}
KNOWN = {SORT_PROFILE: {"nom": "Tri", "parametres": {"needToAssign": True, "wmsEnumNeedId": "SORT"}}}

FEATURE = """Feature: test
  Background:
    Given CARTO- Les implantations suivantes
      | refSiteCode | wmsSectors    | refPackagingCode |
      | 01          | {SECT-MANUEL} | CAR PM           |

  Scenario: palettisation de la dernière UP
    Given PREP- <X> besoins pour l'UP
      | X | wmsEnumNeed.value.id | status.value.id |
      | 1 | PALLETIZE            | TO_DO           |
    And PREP- <X> besoins pour l'UP
      | X | wmsEnumNeed.value.id |
      | 0 | SPEC_PAIRING         |
    When KIS- Ouvrir une interaction "Palettisation"
    Then PREP- <X> besoins pour l'UP
      | X | wmsEnumNeed.value.id |
      | 1 | SPEC_PAIRING         |
"""
CHARACTERISTICS = [("mechanization", "", "wmsPreparationUnit.mechanization"),
                   ("ref_packaging", "", "wmsPreparationUnit.refPackaging.code"), ("wms_sector", "", "")]


def combo(mechanization, packaging, sector, effect, count):
    return {"valeurs": {"mechanization": mechanization, "ref_packaging": packaging, "wms_sector": sector},
            "effet": effect, "nombre": count}


COMBOS = [combo("MECHANIZED", "CAR PM", "SECT-AUTO", True, 40), combo("MECHANIZED", "CAR GM", "SECT-AUTO", True, 12),
          combo("UNMECHANIZED", "CAR PM", "SECT-MANUEL", False, 9), combo("UNMECHANIZED", "CO", "SECT-AUTRE", False, 2)]


class FakeEntity:
    def __init__(self, spec, schema, query, since=""):
        self.label, self.table, self.id, self.need_table, self.variable = spec
        self.readable = True
        self.combos = COMBOS

    def characteristics(self, paths):
        return CHARACTERISTICS

    def combinations(self, characteristics, need):
        return [c for c in self.combos] if need == "PALLETIZE" else [dict(c, effet=False) for c in self.combos]

    def combinations_sql(self, characteristics, need):
        return f"SELECT … besoin {need}"


def test_expected_effects_split_preconditions_from_results_and_skip_absences():
    effects = br.expected_effects(FEATURE)
    assert [(e["besoin"], e["phase"], e["entite"][0]) for e in effects] == [
        ("PALLETIZE", "precondition", "UP"), ("SPEC_PAIRING", "resultat", "UP")]
    assert effects[0]["statut"] == "TO_DO"
    assert {"SECT-MANUEL", "CAR PM", "01"} <= br.feature_values(FEATURE)


def test_rule_conditions_are_evaluated_in_order_with_three_valued_logic():
    facts = {"wmsPreparationUnit.mechanization": "UNMECHANIZED", "wmsPreparationUnit.refPackaging.code": "CAR PM"}
    decision = br.decide(RULE, "PALLETIZE", facts, KNOWN)
    assert decision["effet"] is False and "otherwise" in decision["explication"]
    assert decision["expression"]["description"] == "Pas de palettisation si non mécanisé"
    # Emballage CO : la condition client est fausse, la suivante (par défaut) attribue le besoin (valeur reprise par
    # une expression du Standard sur ce seul besoin)
    facts["wmsPreparationUnit.refPackaging.code"] = "CO"
    assert br.decide(RULE, "PALLETIZE", facts, KNOWN)["effet"] is True
    # Donnée inconnue : condition indécise, jamais une conclusion
    assert br.decide(RULE, "PALLETIZE", {}, KNOWN)["effet"] is None
    assert br.decide(RULE, "SORT", {"wmsPreparationUnit.mechanization": "MIXED"}, KNOWN)["effet"] is True
    assert [e["description"] for e in br.refusing(RULE, "PALLETIZE", KNOWN)] == ["Pas de palettisation si non mécanisé"]
    assert br.atoms("a.b == $E.X() && (c == $E.Y()||!d.code.equals(\"Z\")) && e.any{it}")[-1][1] == "?"


def test_rule_contradicting_planned_data_and_real_cases_are_found_without_run(monkeypatch):
    monkeypatch.setattr(br, "Entity", FakeEntity)
    rules = [{"cle": "x/rule-assign-need", "nom": "AssignNeed", "version": "7", "regle": RULE}]
    effect = br.expected_effects(FEATURE)[0]
    result = br.analyse_effect(effect, "s", None, rules, KNOWN, br.feature_values(FEATURE))
    assert result["nombre"] == 52
    # Secteur écrit dans le test → mécanisation déduite de la copie (tous les objets de ce secteur)
    assert result["prevu"]["mechanization"][0] == "UNMECHANIZED"
    text = " ".join(result["contradictions"])
    assert "AssignNeed" in text and "Pas de palettisation si non mécanisé" in text
    assert "wms_sector prévu(e) « SECT-MANUEL »" in text
    # Cas réel à reprendre : garde l'emballage écrit par le test
    assert result["recommandation"][0]["valeurs"] == {"mechanization": "MECHANIZED", "ref_packaging": "CAR PM",
                                                      "wms_sector": "SECT-AUTO"}
    assert any("Cas réel à reprendre" in line for line in br.report_lines({**result, "scenario": "s", "ligne": 9}))


def test_effect_without_real_case_blocks_a_precondition_but_only_warns_a_result(monkeypatch):
    monkeypatch.setattr(br, "Entity", FakeEntity)
    effects = br.expected_effects(FEATURE)
    pre = br.analyse_effect(dict(effects[0], besoin="SPEC_PAIRING"), "s", None, [], {}, set())
    post = br.analyse_effect(effects[1], "s", None, [], {}, set())
    assert any("aucun cas réel" in c for c in pre["contradictions"])
    assert not post["contradictions"] and any("aucun cas réel" in w for w in post["avertissements"])


def test_profile_meaning_is_read_from_the_copy_or_deduced_from_the_rule():
    assert br.outcome(RULE, SORT_PROFILE, "SORT", KNOWN)[0] is True
    assert br.outcome(RULE, SORT_PROFILE, "PALLETIZE", KNOWN)[0] is False
    assert br.outcome(RULE, NO_NEED, "PALLETIZE", {})[0] is False
    assert br.outcome(RULE, "inconnu", "PALLETIZE", {})[0] is None
    assert br.concerns(RULE, "PALLETIZE", "wmsPreparationUnit") and not br.concerns(RULE, "DUMMY", "wmsPreparationUnit")


def test_active_rules_and_profiles_are_read_from_the_database():
    def query(sql, schema):
        if "kis_business_rule" in sql:
            return [{"code": "x/rule-assign-need@@AssignNeed@@7@@" + json.dumps(RULE)}]
        if "kis_profile_config" in sql:
            return [{"code": SORT_PROFILE + '@@Tri@@{"parameters":{"needToAssign":true,"wmsEnumNeedId":"SORT"}}'}]
        return []
    assert br.active_rules("s", query)[0]["nom"] == "AssignNeed"
    assert br.profiles("s", query)[SORT_PROFILE]["parametres"]["wmsEnumNeedId"] == "SORT"
