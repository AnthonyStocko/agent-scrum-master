"""Données de test choisies sans IA, coût des sessions reprises, correction identifiée appliquée, version de la
référence, heure d'échec lue dans la console, restauration parallèle."""
import importlib
import io
import os
import sys

import pytest

from tests.conftest import write
from tools import cucumber_copied_config as cc
from tools import cucumber_cost as cost
from tools import cucumber_decisions as cd
from tools import cucumber_diagnostic as diag
from tools import cucumber_import as ci
from tools import cucumber_test_selection as ts
from tools import db_restore

FEATURE = "src/test/resources/cucumber/features/clientx/Stock.feature"


@pytest.fixture
def main_module():
    saved = sys.stdout, sys.stderr
    sys.stdout, sys.stderr = io.StringIO(), io.StringIO()
    try:
        return importlib.import_module("main")
    finally:
        sys.stdout, sys.stderr = saved


# --- Coût des sessions reprises -----------------------------------------------------------------------------------

def test_resumed_session_cost_is_the_difference_with_the_previous_cumulative():
    # Claude Code renvoie le coût CUMULÉ de la session reprise (11.33 $ puis 14.45 $ : l'appel a coûté 3.12 $)
    first = cost.call_cost({"cout_usd": 11.3254, "session_id": "s1"}, 0.0)
    assert first["cout_usd"] == 11.3254 and cost.session_base("s1") == 11.3254
    second = cost.call_cost({"cout_usd": 14.4479, "session_id": "s1"}, cost.session_base("s1"))
    assert second["cout_usd"] == 3.1225 and second["cout_session"] == 14.4479
    assert cost.session_base("s1") == 14.4479
    # Budget passé à Claude : cumul précédent + budget restant (sinon l'appel s'arrête d'office)
    assert cost.resumed_call_budget("0.55", 14.4479) == "15.00"
    # Session inconnue : aucun budget passé, coût signalé comme cumul
    assert cost.session_base("inconnue") is None and cost.resumed_call_budget("0.55", None) == ""
    assert cost.call_cost({"cout_usd": 9.0, "session_id": "x"}, None)["cout_cumul_inconnu"] is True


def test_followup_without_known_session_cost_starts_a_new_call(main_module, monkeypatch, capsys):
    calls = []

    def fake(prompt, model, tools, max_turns, run_dir, effort, system_file="", db_log=None, db_step="", resume="",
             **kwargs):
        calls.append(resume)
        return 0, "{}", {"session_id": "neuve", "cout_usd": 1.0}, False
    monkeypatch.setattr(main_module, "run_cucumber_claude", fake)
    lot = {"session": "ancienne", "modele": "opus"}
    main_module.cucumber_followup(lot, "p", "sonnet", "", [], "5", ".")
    assert calls == [""] and "session" not in lot
    assert "Session du lot sans cumul de coût connu" in capsys.readouterr().out
    cost.record_session("connue", 2.0)
    lot = {"session": "connue", "modele": "opus"}
    main_module.cucumber_followup(lot, "p", "sonnet", "", [], "5", ".")
    assert calls[-1] == "connue"


# --- Correction identifiée mais non appliquée ---------------------------------------------------------------------

def test_identified_but_unapplied_correction_is_detected_and_merged():
    fix = {"corrigees": 0, "restantes": [
        {"tag": "@t", "nature": "a_verifier", "cause": "réserve occupée. Correction à faire : choisir une réserve vide "
                                                      "puis remplacer wmsLocationCode. Je ne l'ai pas faite, faute "
                                                      "de budget", "preuve": "Mélange de produits non cohérent"},
        {"tag": "@u", "nature": "a_verifier", "cause": "c", "correction_a_faire": "colonne X → Y"},
        {"tag": "@v", "nature": "hors_test_prouvee", "cause": "c", "preuve": "p", "correction_a_faire": "z"}]}
    pending = diag.pending_corrections(fix, {"@t", "@u", "@v"})
    assert [(p["tag"], p["correction"]) for p in pending] == [
        ("@t", "choisir une réserve vide puis remplacer wmsLocationCode"), ("@u", "colonne X → Y")]
    prompt = diag.apply_pending_prompt(pending)
    assert "APPLIQUE MAINTENANT" in prompt and "Mélange de produits" in prompt and "aucune nouvelle requête" in prompt
    merged = diag.merge_applied(fix, {"corrigees": 1, "corrections": [{"tag": "@t", "correction": "réserve vide"}]})
    assert merged["corrigees"] == 1 and [r["tag"] for r in merged["restantes"]] == ["@u", "@v"]


def test_same_autonomous_data_change_is_listed_once():
    entry = {"tag": "@t", "nature": "validation_humaine", "cause": "c",
             "valeur": {"step": "CONTENT- Le stock suivant", "avant": "A", "apres": "B"}}
    assert len(cd.data_changes_to_apply([entry, dict(entry)], {"@t"})["@t"]) == 1


# --- Données de test choisies sans IA -----------------------------------------------------------------------------

class FakeDb:
    def __init__(self):
        self.sql = []

    def __call__(self, sql, schema):
        self.sql.append(sql)
        if "FROM ref_site" in sql:
            return [{"code": "01@@3148950@@true"}]
        if "GROUP BY ss.code" in sql:
            return [{"code": "STOCK-A@@40"}]
        if "plant_mode = 'NA'" in sql and "SELECT rl.code" in sql:
            return [{"code": "R.01@@STOCK-A@@PREP-A"}, {"code": "R.02@@STOCK-A@@PREP-A"}]
        if "FIXED_PICKING" in sql:
            return [{"code": "P.01@@PREP-A"}]
        if "FROM wms_handling_unit" in sql:
            return []
        if "FROM ref_stock_owner" in sql:
            return [{"code": "CF01@@N/A (standard), QUALITE"}]
        if "JOIN wms_content" in sql:
            return [{"code": "R.OCC"}]
        return []


def test_test_data_are_selected_without_ai_with_their_proof(tmp_path):
    feature = ("Feature: F\n  Scenario: S\n    Given CONTENT- Le stock suivant\n"
               "      | refProductCode | wmsLocationCode | quantity |\n      | P1 | R.OCC | 1 |\n")
    write(str(tmp_path / FEATURE), feature)
    write(str(tmp_path / "src/test/resources/cucumber/features/clientx/Autre.feature"),
          feature.replace("R.OCC", "R.AUTRE"))
    db = FakeDb()
    selection = ts.select("schema_test", db, str(tmp_path), [FEATURE])
    assert [r["code"] for r in selection["reserves"]] == ["R.01", "R.02"] and selection["secteur"] == "STOCK-A"
    assert selection["gs1"] == "3148950" and all(len(c) == 18 and c.startswith("3148950") for c in selection["um"])
    assert all(ts.gs1_check_digit(c[:17]) == c[17] for c in selection["um"])
    # Emplacement déjà cité par un autre test : jamais proposé
    assert any("R.AUTRE" in sql and "NOT IN" in sql for sql in db.sql)
    block = ts.prompt_block(selection, copied=True)
    assert "DONNÉES DE TEST CHOISIES" in block and "R.01" in block and ts.EMPTY_RESERVE_LESSON in block
    assert "jamais un produit de la copie" in block and "Preuves :" in block
    # Réserve occupée dans la copie : refusée au contrôle des fichiers
    errors = ts.occupied_reserves(str(tmp_path), [FEATURE], "schema_test", "2026-11-13T09:56:45", db)
    assert len(errors) == 1 and "R.OCC" in errors[0] and "déjà occupée dans la copie" in errors[0]
    assert ts.select("schema_test", lambda sql, schema: [], str(tmp_path)) == {}
    assert ts.prompt_block({}) == ""


# --- Version réelle de la base copiée -----------------------------------------------------------------------------

def test_reference_is_invalidated_when_the_source_changed_after_the_copy(monkeypatch):
    stored = {"processus Studio": "54|2026-11-06 09:19:09"}
    now = {"processus Studio": "57|2026-11-13 13:52:40"}
    assert "54 version(s)" in cc.version_gap(stored, now, "2026-11-13T09:56:45")[0]
    assert cc.version_gap(now, now, "2026-11-13T09:56:45") == []
    # Référence sans empreinte : déploiement de la source postérieur à la copie
    gap = cc.version_gap({}, now, "2026-11-13T09:56:45")
    assert "après la copie du 2026-11-13 09:56" in gap[0]
    assert cc.version_gap({}, {"processus Studio": "54|2026-11-06 09:19:09"}, "2026-11-13T09:56:45") == []
    profile = {"saisi": {}, cc.REFERENCE_KEY: {"date": "2026-11-13T09:56:45", "source": "u@h:5432/db / cansond"}}
    monkeypatch.setattr(cc, "active", lambda p: True)
    invalidated = []
    monkeypatch.setattr(cc, "invalidate", lambda client, reason: invalidated.append(reason) or True)

    def source(sql, schema):
        assert schema == "cansond"
        return [{"code": "57|2026-11-13 13:52:40"}] if "kis_custom_process_version" in sql else []
    reason = cc.check_source_version("clientx", profile, query=source, force=True)
    assert "version de la base source changée" in reason and invalidated == [reason]
    signature = cc.version_signature("s", lambda sql, schema: [{"code": "3|2026-01-01"}])
    assert set(signature) == {label for label, _t, _c in cc.VERSION_TABLES}


# --- Heure de l'échec lue dans la console -------------------------------------------------------------------------

def test_failure_time_comes_from_the_console_when_the_report_has_none():
    console = "\n".join([
        "[2026-11-15T16:00:00.000Z] Scenario: Mon scénario # src/test/resources/cucumber/features/clientx/Stock.feature:3",
        "[2026-11-15T16:00:05.000Z]     Given CONTENT- Le stock suivant",
        "[2026-11-15T16:00:09.000Z]     Then Rien",
        "[2026-11-15T16:00:20.000Z] Tests run: 1, Failures: 1",
        "[2026-11-15T16:00:21.000Z] [ERROR] src/test/resources/cucumber/features/clientx/Stock.Mon scénario -- Time elapsed: 1 s <<< FAILURE!",
        "[2026-11-15T16:00:21.000Z] java.lang.AssertionError: Pas d'élément",
        "[2026-11-15T16:00:21.000Z] \tat ✽.CONTENT- Le stock suivant(file:///w/src/test/resources/cucumber/features/clientx/Stock.feature:4)",
        "[2026-11-15T16:00:22.000Z] [INFO] fin"])
    results = ci.console_results(console)
    failed = [r for r in results.values() if r["statut"] == "echoue"]
    assert failed and failed[0]["heure"].startswith("2026-11-15T16:00:05")      # ligne du step en échec
    # Step introuvable dans le bloc : fin du scénario (début + durée)
    results = ci.console_results(console.replace("Given CONTENT- Le stock suivant", "Given autre chose"))
    assert next(r for r in results.values() if r["statut"] == "echoue")["heure"].startswith("2026-11-15T16:00:20")


# --- Restauration -------------------------------------------------------------------------------------------------

def test_restore_runs_pg_restore_in_parallel(monkeypatch):
    monkeypatch.delenv("RESTORE_JOBS", raising=False)
    assert db_restore.restore_jobs() == 4
    monkeypatch.setenv("RESTORE_JOBS", "1")
    assert db_restore.restore_jobs() == 1
    monkeypatch.setenv("RESTORE_JOBS", "x")
    assert db_restore.restore_jobs() == 4


def test_init_fingerprint_changes_with_init_files(tmp_path, main_module):
    rel = "src/test/resources/cucumber/features/clientx/Init.feature"
    write(os.path.join(str(tmp_path), rel), "Feature: Init\n")
    profile = {"init_features": [rel]}
    first = main_module.init_fingerprint(str(tmp_path), profile)
    assert first == main_module.init_fingerprint(str(tmp_path), profile)
    write(os.path.join(str(tmp_path), rel), "Feature: Init\n  Scenario: autre\n")
    assert main_module.init_fingerprint(str(tmp_path), profile) != first
