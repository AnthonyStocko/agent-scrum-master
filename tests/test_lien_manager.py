"""Liens préremplis d'agent-manager-savoye (tools/lien_manager.py, app.apply_manager_link)."""
import os

import pytest

from agent_socle import lien_manager as lm

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PAGES = ("integration", "lancement", "cucumber", "assistant")


def test_lire_valide_et_filtre():
    assert lm.lire({"page": "cucumber", "ticket": "ODA-1"}, PAGES) == {}          # pas un lien du manager
    lien = lm.lire({"source": ["manager"], "page": "cucumber", "ticket": "oda-12", "projet": "clientx",
                    "question": " Pourquoi ? ", "contexte": "Lancer\nles tests", "demande": "ignorée"}, PAGES)
    assert lien == {"page": "cucumber", "ticket": "ODA-12", "projet": "clientx", "question": "Pourquoi ?",
                    "contexte": "Lancer les tests"}
    assert lm.lire({"source": "manager", "page": "inconnue", "ticket": "pas-un ticket", "projet": "a/b"}, PAGES) == {}
    assert len(lm.lire({"source": "manager", "question": "x" * 5000}, PAGES)["question"]) == lm.TEXTE_MAX


def test_signature_projet_et_ticket():
    assert lm.signature({"b": "2", "a": "1"}) == "a=1|b=2"
    assert lm.projet_connu("Client-X", ["clientx", "autre"]) == "clientx"
    assert lm.projet_connu("inconnu", ["clientx"]) == "" and lm.projet_connu("", ["clientx"]) == ""
    assert lm.projet_jira("ODA-12") == "ODA" and lm.projet_jira("") == ""


def test_app_applique_le_lien_une_fois(monkeypatch, tmp_path):
    testing = pytest.importorskip("streamlit.testing.v1")
    from tools import cucumber_detection, standard_version_task
    from agent_socle import jira_tools
    monkeypatch.chdir(tmp_path)
    (tmp_path / ".env").write_text("", encoding="utf-8")
    monkeypatch.delenv("AGENT_AUTH", raising=False)
    monkeypatch.setattr(cucumber_detection, "start", lambda *a, **k: False)
    monkeypatch.setattr(standard_version_task, "start", lambda *a, **k: False)
    monkeypatch.setattr(jira_tools, "get_jira_issue_summary", lambda key: "Titre du ticket")
    at = testing.AppTest.from_file(os.path.join(ROOT, "app.py"), default_timeout=120)
    at.query_params.update({"source": "manager", "ticket": "ODA-12", "contexte": "Cadrer le ticket."})
    at.run()
    assert not at.exception, [e.value for e in at.exception]
    assert at.session_state["nav_page"] == "lancement"
    assert at.session_state["jira_tickets"] == ["ODA-12"]
    assert at.session_state["jira_titles"] == {"ODA-12": "Titre du ticket"}
    text = " ".join(m.value for m in at.markdown)
    assert "Ouvert depuis le manager" in text
    # Page Lancement dans agent-lancement (8506) : le ticket lui est transmis (test de la page déplacé)
    assert "ticket=ODA-12" in at.get("link_button")[0].proto.url
    # Choix fait ensuite dans l'IHM : le lien n'est pas réappliqué
    at.session_state["nav_page"] = "integration"
    at.run()
    assert at.session_state["nav_page"] == "integration"


def test_app_question_preparee_jamais_envoyee_seule(monkeypatch, tmp_path):
    testing = pytest.importorskip("streamlit.testing.v1")
    from tools import assistant, cucumber_detection, standard_version_task
    monkeypatch.chdir(tmp_path)
    (tmp_path / ".env").write_text("", encoding="utf-8")
    monkeypatch.delenv("AGENT_AUTH", raising=False)
    monkeypatch.setattr(cucumber_detection, "start", lambda *a, **k: False)
    monkeypatch.setattr(standard_version_task, "start", lambda *a, **k: False)
    saved = []
    monkeypatch.setattr(assistant, "load_history", lambda: [])
    monkeypatch.setattr(assistant, "save_history", lambda messages: saved.append(messages))
    at = testing.AppTest.from_file(os.path.join(ROOT, "app.py"), default_timeout=120)
    at.query_params.update({"source": "manager", "question": "Où en est le ticket ODA-12 ?"})
    at.run()
    assert not at.exception, [e.value for e in at.exception]
    assert at.session_state["nav_page"] == "assistant"
    assert at.session_state["manager_question"] == "Où en est le ticket ODA-12 ?"
    assert "Où en est le ticket ODA-12 ?" in " ".join(m.value for m in at.markdown)
    assert saved == []                                  # rien n'est envoyé à l'agent sans clic
