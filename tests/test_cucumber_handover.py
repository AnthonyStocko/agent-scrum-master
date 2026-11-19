"""Dossier de passation des corrections après exécution (tools/cucumber_handover.py) et session neuve."""
import importlib
import io
import json
import os
import sys

import pytest

from tests.conftest import CLIENT, CLIENT_TAG, git, write
from tools import cucumber_handover as ch

RES = "src/test/resources/cucumber"
REL = f"{RES}/features/{CLIENT}/Stock.feature"
STEPS_REL = "src/test/java/client/StockSteps.java"
TAG = "@agent_clientx_1_1"


@pytest.fixture
def main_module():
    saved = sys.stdout, sys.stderr
    sys.stdout, sys.stderr = io.StringIO(), io.StringIO()
    try:
        return importlib.import_module("main")
    finally:
        sys.stdout, sys.stderr = saved


FIX = {"corrigees": 1,
       "corrections": [{"tag": TAG, "cause": "emplacement SITEA-01 inexistant", "correction": "emplacement changé"}],
       "preuves_base": [{"tag": TAG, "tables": ["loc_location"], "requete": "SELECT code FROM loc_location WHERE x",
                         "resultat": "0 ligne", "conclusion": "donnée absente"}],
       "restantes": [{"tag": TAG, "nature": "a_verifier", "cause": "le statut du stock peut bloquer"},
                     {"tag": "@autre", "nature": "a_verifier", "cause": "autre scénario"}]}


def test_record_attempt_outcome_and_block(monkeypatch):
    monkeypatch.delenv("CUCUMBER_HANDOVER", raising=False)
    lot = {"base": [{"commande": "query", "tables": ["stk_stock"], "ok": False, "erreur": "column \"qty\" does not exist"},
                    {"commande": "query", "tables": ["stk_stock"], "ok": False, "erreur": "column \"qty\" does not exist"},
                    {"commande": "describe", "tables": ["loc_location"], "ok": True, "erreur": ""}]}
    failing = {"id": "f::S", "statut": "invalide", "step": "Given X", "motif": "Index 0 out of bounds (essai 1)"}
    ch.record_attempt(lot, "correction 1", FIX, {TAG: failing})
    entry = lot["passation"][TAG][0]
    assert entry["causes"] == [{"cause": "emplacement SITEA-01 inexistant", "correction": "emplacement changé",
                                "prouvee": False}]
    assert entry["hypotheses"] == ["le statut du stock peut bloquer"] and entry["preuves"][0]["tables"] == ["loc_location"]
    # Même échec au run suivant (valeur variable masquée) : hypothèse réfutée
    ch.record_outcome(lot, {TAG: {**failing, "motif": "Index 0 out of bounds (essai 2)"}}, {TAG})
    assert entry["resultat"] == "même échec : hypothèse réfutée"
    text = ch.block(lot, [TAG])
    assert "DOSSIER DE PASSATION" in text and "supposée (non prouvée) : emplacement SITEA-01 inexistant" in text
    assert "hypothèse réfutée" in text and "preuve base [loc_location] donnée absente — 0 ligne" in text
    assert "hypothèse non vérifiée : le statut du stock peut bloquer" in text and "autre scénario" not in text
    # Commande base en erreur citée une fois, sans les commandes réussies
    assert text.count("column \"qty\" does not exist") == 1 and "describe" not in text
    # Tentative suivante : verte
    ch.record_attempt(lot, "correction 2", {"corrections": [{"tag": TAG, "cause": "c", "correction": "d",
                                                              "cause_prouvee": {"erreur": "E"}}]}, {TAG: failing})
    ch.record_outcome(lot, {TAG: {"statut": "pertinent"}}, {TAG})
    assert lot["passation"][TAG][1]["resultat"] == "vert" and lot["passation"][TAG][1]["causes"][0]["prouvee"]
    assert ch.block({}, [TAG]) == ""


def test_outcome_not_rerun_and_unreadable_answer():
    lot = {}
    ch.record_attempt(lot, "correction 1", {}, {TAG: {"motif": "m"}})
    assert "réponse illisible" in lot["passation"][TAG][0]["causes"][0]["cause"]
    ch.record_outcome(lot, {TAG: {"motif": "m"}}, set())
    assert lot["passation"][TAG][0]["resultat"] == "non relancé"
    # Un résultat déjà noté n'est pas écrasé
    ch.record_outcome(lot, {TAG: {"statut": "pertinent"}}, {TAG})
    assert lot["passation"][TAG][0]["resultat"] == "non relancé"


def test_block_is_bounded_and_keeps_latest_attempts(monkeypatch):
    lot = {}
    for i in range(ch.MAX_ATTEMPTS + 3):
        ch.record_attempt(lot, f"correction {i + 1}", {"corrections": [{"tag": TAG, "cause": f"cause {i} " + "x" * 220,
                                                                          "correction": "y" * 230}]},
                          {TAG: {"motif": "z" * 300}})
    assert len(lot["passation"][TAG]) == ch.MAX_ATTEMPTS
    monkeypatch.setattr(ch, "MAX_CHARS", 1500)
    text = ch.block(lot, [TAG])
    assert len(text) <= 1500 + 600 and "tentatives plus anciennes omises" in text
    assert f"correction {ch.MAX_ATTEMPTS + 3}" in text and "correction 4 " not in text


def test_disabled(monkeypatch):
    monkeypatch.setenv("CUCUMBER_HANDOVER", "0")
    lot = {}
    ch.record_attempt(lot, "correction 1", FIX, {TAG: {"motif": "m"}})
    assert lot == {} and ch.block({"passation": {TAG: [{"etape": "x"}]}}, [TAG]) == ""


def test_fix_loop_runs_fresh_sessions_with_handover(repo, main_module, monkeypatch):
    """2 corrections : la 2e part en session neuve (pas de --resume) avec la cause réfutée de la 1re."""
    write(os.path.join(repo, REL), f"{CLIENT_TAG}\nFeature: Stock\n\n  {TAG}\n  Scenario: Bloqué\n    Then b\n")
    write(os.path.join(repo, STEPS_REL), "public class StockSteps {\n}\n")
    git(repo, "add", "-A")
    git(repo, "commit", "-qm", "base")
    calls = []

    def verify_lot(mode, worktree, base, build, files, scenarios, *rest):
        return {"scenarios": {TAG: {"id": f"{REL}::Bloqué", "statut": "invalide", "niveaux": {},
                                    "motif": f"Index 0 out of bounds (essai {len(calls)})"}}, "builds": []}

    def claude(prompt, *args, **kwargs):
        calls.append((prompt, kwargs.get("resume", "")))
        with open(os.path.join(repo, REL), "a", encoding="utf-8") as f:
            f.write("    # correction %d\n" % len(calls))
        return 0, json.dumps(FIX), {"session_id": f"s{len(calls)}"}, False
    monkeypatch.setattr(main_module.cucumber_verify, "verify_lot", verify_lot)
    monkeypatch.setattr(main_module, "run_cucumber_claude", claude)
    monkeypatch.setattr(main_module, "render_prompt", lambda name, **values: values["erreurs"])
    monkeypatch.setattr(main_module, "report_token_usage", lambda *a, **k: None)
    monkeypatch.setattr(main_module, "verify_cucumber_lot", lambda *a, **k: {"ok": True})
    monkeypatch.setattr(main_module.cucumber_cost, "session_base", lambda s: 0.0)
    monkeypatch.setattr(main_module.cucumber_cost, "session_warm", lambda s: True)
    monkeypatch.setenv("CUCUMBER_MAX_RETRACT_PCT", "100")
    monkeypatch.delenv("CUCUMBER_HANDOVER", raising=False)
    monkeypatch.delenv("CLAUDE_MODEL_CUCUMBER_VERIFY_FIX", raising=False)
    monkeypatch.setenv("CUCUMBER_VERIFY_FIX_MAX", "3")
    lot = {"numero": 1, "tags": {TAG: f"{REL}::Bloqué"}, "indicateurs": {"scenarios": []}, "session": "lot-session"}
    main_module.verify_execution_lot(lot, [], "1/1", repo, "HEAD", [REL], [], "local", "", CLIENT, "", "", "", {},
                                     "sonnet", "K", "/run", lambda u: None,
                                     context={"conventions": {"client_tag": CLIENT_TAG}})
    # Session du lot chaude, mais jamais reprise par une correction après exécution
    assert calls and all(resume == "" for _, resume in calls)
    assert "DOSSIER DE PASSATION" not in calls[0][0]
    assert "DOSSIER DE PASSATION" in calls[1][0] and "même échec : hypothèse réfutée" in calls[1][0]
    assert "emplacement SITEA-01 inexistant" in calls[1][0]
    assert lot["passation"][TAG][0]["resultat"] == "même échec : hypothèse réfutée"


def test_fix_loop_resumes_lot_session_when_disabled(repo, main_module, monkeypatch):
    write(os.path.join(repo, REL), f"{CLIENT_TAG}\nFeature: Stock\n\n  {TAG}\n  Scenario: Bloqué\n    Then b\n")
    write(os.path.join(repo, STEPS_REL), "public class StockSteps {\n}\n")
    git(repo, "add", "-A")
    git(repo, "commit", "-qm", "base")
    calls = []

    def claude(prompt, *args, **kwargs):
        calls.append((prompt, kwargs.get("resume", "")))
        return 0, "{}", {"session_id": "lot-session"}, False
    monkeypatch.setattr(main_module.cucumber_verify, "verify_lot", lambda *a, **k: {"scenarios": {TAG: {
        "id": f"{REL}::Bloqué", "statut": "invalide", "niveaux": {}, "motif": "KO"}}, "builds": []})
    monkeypatch.setattr(main_module, "run_cucumber_claude", claude)
    monkeypatch.setattr(main_module, "render_prompt", lambda name, **values: values["erreurs"])
    monkeypatch.setattr(main_module, "report_token_usage", lambda *a, **k: None)
    monkeypatch.setattr(main_module, "verify_cucumber_lot", lambda *a, **k: {"ok": True})
    monkeypatch.setattr(main_module.cucumber_cost, "session_base", lambda s: 0.0)
    monkeypatch.setattr(main_module.cucumber_cost, "session_warm", lambda s: True)
    monkeypatch.setenv("CUCUMBER_MAX_RETRACT_PCT", "100")
    monkeypatch.setenv("CUCUMBER_HANDOVER", "0")
    monkeypatch.delenv("CLAUDE_MODEL_CUCUMBER_VERIFY_FIX", raising=False)
    lot = {"numero": 1, "tags": {TAG: f"{REL}::Bloqué"}, "indicateurs": {"scenarios": []}, "session": "lot-session"}
    main_module.verify_execution_lot(lot, [], "1/1", repo, "HEAD", [REL], [], "local", "", CLIENT, "", "", "", {},
                                     "sonnet", "K", "/run", lambda u: None,
                                     context={"conventions": {"client_tag": CLIENT_TAG}})
    assert calls[0][1] == "lot-session" and "passation" not in lot
