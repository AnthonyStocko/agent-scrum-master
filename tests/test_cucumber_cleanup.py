"""Nettoyage des données des tests générés : installation, contrôle avant livraison, preuve après deux runs."""
import json
import os

from tools import cucumber_cleanup as cc
from tools import cucumber_copied_config, cucumber_verify

RUNNER = '''package cucumber;
@RunWith(Cucumber.class)
@CucumberOptions(
        features = "src/test/resources",
        plugin = {
                "pretty"
        }
)
public class RunnerTest {}
'''
FEATURE = '''@CLIENT
Feature: test
  Background:
    Given REPORT- Initialisation du document "DOC_BL"
    And REPORT- Modification du document en contexte
      | defaultExchangeCodes |
      | imprimante-test      |
    And INIT- Intégration des fichiers GS1
      | fileName       |
      | CIN-TEST.xml   |
    And CARTO- Les implantations suivantes
      | refSiteCode | refStockOwnerCode | refProductCode |
      | S1          | DO1               | P-TEST         |

  Scenario: s
    When PREP- Création de quelque chose de nouveau
      | code |
      | X    |
    Then PREP- <X> besoins pour l'UP
      | X | wmsEnumNeed.value.id |
      | 1 | PALLETIZE            |
'''
CIN = '''<root xmlns:a="urn:x"><catalogueItemNotification><tradeItem>
  <additionalTradeItemIdentification additionalTradeItemIdentificationTypeCode="FOR_INTERNAL_USE_1">P-TEST</additionalTradeItemIdentification>
</tradeItem><informationProviderOfTradeItem>
  <additionalPartyIdentification additionalPartyIdentificationTypeCode="FOR_INTERNAL_USE_1">DO1</additionalPartyIdentification>
</informationProviderOfTradeItem></catalogueItemNotification></root>'''
REL = "src/test/resources/cucumber/features/client/T/TEST.feature"


def project(tmp_path):
    root = tmp_path / "wt"
    (root / "src/test/java/cucumber").mkdir(parents=True)
    (root / "src/test/java/cucumber/RunnerTest.java").write_text(RUNNER, encoding="utf-8")
    (root / "src/test/resources/cucumber/features/client/T").mkdir(parents=True)
    (root / REL).write_text(FEATURE, encoding="utf-8")
    (root / "src/test/resources/cucumber/files/client/T").mkdir(parents=True)
    (root / "src/test/resources/cucumber/files/client/T/CIN-TEST.xml").write_text(CIN, encoding="utf-8")
    return str(root)


def test_install_copies_the_runtime_and_registers_the_plugin_once(tmp_path):
    root = project(tmp_path)
    changed = cc.install(root)
    assert "src/test/java/cucumber/nettoyage/NettoyageHooks.java" in changed
    assert "src/test/resources/cucumber/nettoyage/catalogue.json" in changed
    runner = open(os.path.join(root, "src/test/java/cucumber/RunnerTest.java"), encoding="utf-8").read()
    assert runner.count(cc.PLUGIN) == 1 and '"pretty"' in runner
    assert cc.install(root) == []          # déjà à jour : rien n'est réécrit


def test_creation_step_without_cleanup_is_a_lot_error_and_project_catalogue_is_checked(tmp_path):
    root = project(tmp_path)
    cc.install(root)
    errors = cc.check_files(root, [REL])
    assert len(errors) == 1 and "PREP- Création de quelque chose de nouveau" in errors[0]
    assert ":16 :" in errors[0]
    # Entrée ajoutée au catalogue du projet, avec un step de nettoyage inexistant : refusé
    path = os.path.join(root, "src/test/resources/cucumber/nettoyage", cc.PROJECT_CATALOGUE)
    with open(path, "w", encoding="utf-8") as f:
        json.dump({"etapes": [{"step": "PREP- Création de quelque chose de nouveau", "objet": "chose",
                               "mode": "suppression",
                               "nettoyage": [{"step": "PREP- Suppression de la chose", "classe": "cucumber.X",
                                              "methode": "supprimer", "table": {"code": "{code}"}}]}]}, f)
    os.makedirs(os.path.join(root, "src/test/java/cucumber/features"), exist_ok=True)
    with open(os.path.join(root, "src/test/java/cucumber/features/Y.java"), "w", encoding="utf-8") as f:
        f.write("package cucumber.features;\nclass Y { public void autre(DataTable t) {} }\n")
    errors = cc.check_files(root, [REL])
    assert len(errors) == 1 and "introuvable (cucumber.X.supprimer)" in errors[0]
    with open(os.path.join(root, "src/test/java/cucumber/X.java"), "w", encoding="utf-8") as f:
        f.write("package cucumber;\nclass X { public void supprimer(DataTable t) {} }\n")
    assert cc.check_files(root, [REL]) == []


def test_agent_catalogue_steps_exist_in_the_standard_library_sources(tmp_path):
    # Chaque step de nettoyage du catalogue est une méthode publique (DataTable) de la classe indiquée
    methods = {(a["classe"], a["methode"]) for s in cc.catalogue()
               for a in list(s.get("nettoyage") or []) + [x for r in s.get("fichiers") or [] for x in r["nettoyage"]]}
    assert ("cucumber.features.wmsCartography.WmsPlantConfigStep", "deletePlantConfig") in methods
    assert cc.check_catalogue(cc.catalogue(), methods) == []
    assert cc.check_catalogue([{"step": "s", "mode": "suppression"}], set()) == ["« s » : aucun step de nettoyage"]


def test_created_objects_are_read_from_tables_and_integrated_files_and_left_overs_found(tmp_path):
    root = project(tmp_path)
    objects = cc.created_objects(root, REL)
    by_object = {o["objet"]: o for o in objects}
    assert by_object["produit"]["sql"].endswith("WHERE code = 'P-TEST'")
    assert "p.code = 'P-TEST'" in by_object["implantation"]["sql"]
    assert by_object["document"]["sql"].endswith("WHERE d.code = 'DOC_BL'")
    answers = {"produit": "CLOSED", "implantation": "1", "document": ""}

    def query(sql, schema):
        name = next(o["objet"] for o in objects if o["sql"] == sql)
        return [{"code": answers[name]}]
    before = cc.snapshot(objects, "s", query)
    found = cc.leftovers(objects, "s", query, before)
    assert found["toleres"] == ["produit P-TEST, DO1 : CLOSED (conserve, toléré)"]
    assert found["restes"] == ["implantation S1, DO1, P-TEST : encore présent (1)"]
    assert found["propres"] == ["document DOC_BL : vide (comme avant)"]
    answers["document"] = "imprimante-test"
    assert any("non restauré" in r for r in cc.leftovers(objects, "s", query, before)["restes"])


def test_a_test_is_green_only_if_the_cleanup_proof_passes():
    ok = {"client": ("ok", "passe"), "mutation": ("ok", "échoue comme attendu")}
    assert cucumber_verify.final_status(ok) == ("pertinent", "")
    assert cucumber_verify.final_status({**ok, "nettoyage": ("ko", "données restantes")}) == \
        ("invalide", "nettoyage : données restantes")
    assert cucumber_verify.final_status({**ok, "nettoyage": ("nv", "x")})[0] == "non_verifie"


def test_second_run_of_the_proof_does_not_restore_the_reference():
    restored, ready = [], []
    guard = cucumber_copied_config.RunGuard("c", {"cucumber_schema_test": "s"}, log=lambda m: None,
                                            restore=lambda *a: restored.append(a) or {"ok": True},
                                            restore_reference=False, on_ready=lambda: ready.append(1))
    guard.ref = {"dump": "d"}
    import tools.cucumber_copied_config as module
    original = (module.check_source_version, module.reference_problem)
    module.check_source_version, module.reference_problem = (lambda *a, **k: ""), (lambda p: "")
    try:
        assert guard.prepare() == ""
    finally:
        module.check_source_version, module.reference_problem = original
    assert restored == [] and guard.info["sans_restauration"] and ready == []


def test_verify_lot_runs_the_second_run_without_restore_right_after_the_client_run(monkeypatch, tmp_path):
    from tests.conftest import write
    from tests.test_cucumber_verify import OVERRIDE_FEATURE
    rel = "src/test/resources/cucumber/features/acme/prix.feature"
    write(str(tmp_path / rel), OVERRIDE_FEATURE)
    calls, snapshots = [], []

    def run_tags(mode, worktree, base, build_fn, tags, env, overrides, label, sources=None, restore_reference=True,
                 on_ready=None):
        calls.append((label, restore_reference))
        if on_ready:
            on_ready()
        mutated = label.endswith("mutation")
        result = ({"statut": "echoue", "step": "Then le prix", "phase": "then", "message": "x",
                   "nature": "assertion", "exception": ""} if mutated else
                  {"statut": "passe", "step": "", "phase": "", "message": ""})
        return {"ok": True, "message": "ok", "build": "b", "resultats": {(rel, "Prix client"): result}}
    verdicts = {"value": ("ko", "données restantes après deux runs : stock P1 : encore présent (3)")}
    monkeypatch.setattr(cucumber_verify, "run_tags", run_tags)
    monkeypatch.setattr(cucumber_verify, "cleanup_proof", lambda worktree, features, sources=None: {
        "avant": lambda: snapshots.append(1), "juger": lambda second, key: verdicts["value"]})
    scenarios, kinds = {"@agent_acme_1_1": f"{rel}::Prix client"}, {"@agent_acme_1_1": "extension"}
    result = cucumber_verify.verify_lot("jenkins", str(tmp_path), "HEAD", None, [rel], scenarios, kinds, "lot1")
    # Second run sans restauration juste après le run client, avant la mutation ; relevé fait au premier run
    assert [c for c in calls if not c[0].endswith("mutation")] == [("lot1-client", True), ("lot1-nettoyage", False)]
    assert snapshots == [1]
    assert result["scenarios"]["@agent_acme_1_1"]["statut"] == "invalide"
    assert "nettoyage : données restantes" in result["scenarios"]["@agent_acme_1_1"]["motif"]
    verdicts["value"] = ("ok", "deux runs verts, base propre")
    result = cucumber_verify.verify_lot("jenkins", str(tmp_path), "HEAD", None, [rel], scenarios, kinds, "lot1")
    assert result["scenarios"]["@agent_acme_1_1"]["statut"] == "pertinent"
