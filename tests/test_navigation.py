"""
Navigation : seule la page choisie est exécutée ; hors des pages Cucumber, aucune tâche de fond ni aucun fragment
à rafraîchissement automatique (l'application ne se réexécute plus d'elle-même sur l'onglet Intégration).
"""
import os
import time

from streamlit.testing.v1 import AppTest

from agent_socle import ui_timer

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _app(monkeypatch, tmp_path, page: str = "") -> tuple:
    monkeypatch.chdir(tmp_path)
    (tmp_path / ".env").write_text("", encoding="utf-8")
    monkeypatch.delenv("AGENT_AUTH", raising=False)
    calls = []
    at = AppTest.from_file(os.path.join(ROOT, "app.py"), default_timeout=120)
    if page:
        at.session_state["nav_page"] = page
    at.run()
    assert not at.exception, [e.value for e in at.exception]
    return at, calls


def test_integration_page_by_default_runs_nothing_in_background(monkeypatch, tmp_path):
    at, calls = _app(monkeypatch, tmp_path)
    assert at.session_state["nav_page"] == "integration"
    # Rien des autres pages : ni la configuration, ni l'onglet Cucumber et ses tâches de fond
    assert not [s for s in at.subheader if "Configuration globale" in s.value]
    assert calls == []


def test_only_the_chosen_page_is_executed(monkeypatch, tmp_path):
    at, _ = _app(monkeypatch, tmp_path, "configuration")
    assert [s for s in at.subheader if "Configuration globale" in s.value]
    assert "1. Critères de Recherche Jira" not in " ".join(m.value for m in at.markdown)


def test_set_timer_outside_a_fragment_does_nothing():
    assert ui_timer.set_timer(3) is False
    assert ui_timer.set_timer(None) is False

