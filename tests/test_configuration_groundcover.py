"""
Page Configuration : la configuration Groundcover vient du composant commun (agent_socle.ihm_groundcover), sans
changement de comportement — mêmes champs, « Connect to MCP » analysé à l'enregistrement (aucun jeton dans le .env),
adresse refusée = rien d'enregistré ; variables transmises par le manager en lecture seule, jamais réécrites.
"""
import os

import pytest
from dotenv import dotenv_values
from streamlit.testing.v1 import AppTest

from agent_socle import config_commune

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
URL = "https://mcp.groundcover.com/api/mcp"
VARIABLES = ("GROUNDCOVER_API_KEY", "GROUNDCOVER_BACKEND_ID", "GROUNDCOVER_API_URL", "CUCUMBER_GROUNDCOVER_FILTER",
             "GROUNDCOVER_MCP_URL", "GROUNDCOVER_MCP_HEADERS", "GROUNDCOVER_MCP_TOOLS")


@pytest.fixture(autouse=True)
def _sans_manager(monkeypatch):
    for nom in VARIABLES:
        monkeypatch.delenv(nom, raising=False)
    config_commune.relever({})
    yield
    config_commune.relever({})


def _configuration(monkeypatch, tmp_path, env: str = "") -> AppTest:
    monkeypatch.chdir(tmp_path)
    (tmp_path / ".env").write_text(env, encoding="utf-8")
    monkeypatch.delenv("AGENT_AUTH", raising=False)
    at = AppTest.from_file(os.path.join(ROOT, "app.py"), default_timeout=120)
    at.session_state["nav_page"] = "configuration"
    at.run()
    assert not at.exception, [e.value for e in at.exception]
    return at


def _champ(at, label):
    return next(t for t in at.text_input if t.label == label)


def _enregistrer(at):
    next(b for b in at.button if "Enregistrer la configuration complète" in b.label).click().run()
    assert not at.exception, [e.value for e in at.exception]


def test_champs_groundcover_toujours_affiches(monkeypatch, tmp_path):
    at = _configuration(monkeypatch, tmp_path, "GROUNDCOVER_API_URL=https://api.groundcover.com\n")
    labels = {t.label for t in at.text_input}
    assert {"GROUNDCOVER_API_KEY", "GROUNDCOVER_BACKEND_ID", "GROUNDCOVER_API_URL", "CUCUMBER_GROUNDCOVER_FILTER",
            "Adresse du serveur MCP Groundcover", "En-têtes non secrets (facultatif)",
            "Outils autorisés (facultatif)"} <= labels
    assert _champ(at, "GROUNDCOVER_API_URL").value == "https://api.groundcover.com"
    assert [m for m in at.markdown if "Serveurs MCP" in m.value]
    assert [c for c in at.checkbox if c.label == "Groundcover activé"]


def test_connect_to_mcp_enregistre_sans_jeton(monkeypatch, tmp_path):
    at = _configuration(monkeypatch, tmp_path)
    _champ(at, "Adresse du serveur MCP Groundcover").input(
        f'claude mcp add groundcover {URL} --header "Authorization: Bearer secret-abc" --header "X-Timezone: UTC"')
    _champ(at, "CUCUMBER_GROUNDCOVER_FILTER").input(" namespace:essai ")
    _enregistrer(at)
    saved = dotenv_values(tmp_path / ".env")
    assert saved["GROUNDCOVER_MCP_URL"] == URL and saved["GROUNDCOVER_MCP_HEADERS"] == "X-Timezone: UTC"
    assert saved["CUCUMBER_GROUNDCOVER_FILTER"] == "namespace:essai"
    assert "secret-abc" not in (tmp_path / ".env").read_text(encoding="utf-8")


def test_adresse_refusee_rien_enregistre(monkeypatch, tmp_path):
    at = _configuration(monkeypatch, tmp_path)
    _champ(at, "Adresse du serveur MCP Groundcover").input("https://x.atlassian.net/mcp")
    _enregistrer(at)
    assert [e for e in at.error if "Configuration non enregistrée" in e.value]
    assert (tmp_path / ".env").read_text(encoding="utf-8") == ""


def test_variables_du_manager_en_lecture_seule_jamais_reecrites(monkeypatch, tmp_path):
    environ = {config_commune.VARIABLE_LISTE: "GROUNDCOVER_API_KEY,CUCUMBER_GROUNDCOVER_FILTER",
               "GROUNDCOVER_API_KEY": "cle-du-manager", "CUCUMBER_GROUNDCOVER_FILTER": "namespace:manager"}
    for nom, valeur in environ.items():
        monkeypatch.setenv(nom, valeur)
    config_commune.relever(environ)
    at = _configuration(monkeypatch, tmp_path)
    cle, filtre = _champ(at, "GROUNDCOVER_API_KEY"), _champ(at, "CUCUMBER_GROUNDCOVER_FILTER")
    assert cle.disabled and filtre.disabled and cle.value == "" and filtre.value == "namespace:manager"
    _enregistrer(at)
    saved = dotenv_values(tmp_path / ".env")
    assert "GROUNDCOVER_API_KEY" not in saved and "CUCUMBER_GROUNDCOVER_FILTER" not in saved
    assert "GROUNDCOVER_API_URL" in saved
