"""Consultations en lecture seule pour agent-manager-savoye (tools/lecture_manager.py, mcp_lecture.py)."""
import asyncio
import json
import os
import re
from datetime import datetime, timedelta
from unittest import mock

import pytest

from tools import lecture_manager as lecture
from agent_socle import livraisons
from agent_socle import publication


@pytest.fixture
def racine(tmp_path, monkeypatch):
    monkeypatch.setattr(publication, "PUBLICATION_DIR", str(tmp_path / "publication"))
    (tmp_path / "ODA-1-cadrage.md").write_text("# Besoin\ntexte\n# Solution\nfaite\n", encoding="utf-8")
    (tmp_path / "ODA-2-cadrage.md").write_text("# Besoin\npubliée\n", encoding="utf-8")
    publication.record_publication("ODA-2", "# Besoin\npubliée\n")
    return str(tmp_path)


def test_analyses_et_etat(racine):
    texte = lecture.analyses(racine)
    assert "ODA-1" in texte and "← à valider" in texte.split("ODA-1")[1].splitlines()[0]
    assert "Publiée dans Jira" in texte.split("ODA-2")[1].splitlines()[0]
    detail = lecture.analyse("oda-1", racine)
    assert "brouillon" in detail and "Besoin · Solution" in detail
    assert "Aucune analyse de ODA-3" in lecture.analyse("ODA-3", racine)
    with pytest.raises(lecture.LectureErreur):
        lecture.analyse("../secret", racine)


def test_ticket_avec_le_compte_de_lecture(monkeypatch, racine):
    monkeypatch.setattr(lecture, "WORKSPACE_DIR", racine)
    monkeypatch.setenv("JIRA_DOMAIN", "societe.atlassian.net")
    monkeypatch.setenv("JIRA_EMAIL", "ecriture@exemple.com")
    monkeypatch.setenv("JIRA_API_TOKEN", "jeton-ecriture")
    monkeypatch.setenv("JIRA_READ_EMAIL", "lecture@exemple.com")
    monkeypatch.setenv("JIRA_READ_API_TOKEN", "jeton-lecture")
    reponse = mock.Mock(status_code=200)
    reponse.json.return_value = {"fields": {"summary": "Corriger la vague", "status": {"name": "En cours"},
                                            "issuetype": {"name": "Bug"}, "assignee": None,
                                            "parent": {"key": "ODA-9", "fields": {"summary": "Épopée"}}}}
    with mock.patch.object(lecture.requests, "get", return_value=reponse) as get:
        texte = lecture.ticket("ODA-1")
    assert get.call_args.kwargs["auth"] == ("lecture@exemple.com", "jeton-lecture")
    assert "Corriger la vague" in texte and "En cours" in texte and "non assigné" in texte and "ODA-9" in texte


def test_builds_des_jobs_configures(monkeypatch):
    monkeypatch.setenv("JENKINS_CUCUMBER_JOB", "JOB_NUIT")
    monkeypatch.setenv("CUCUMBER_VERIFY_JOB", "")
    monkeypatch.setenv("CUCUMBER_RUN_JOB", "JOB_NUIT")
    builds = [{"numero": 12, "resultat": None, "debut": 1763456400000, "duree": 0},
              {"numero": 11, "resultat": "SUCCESS", "debut": 1763452800000, "duree": 600000}]
    with mock.patch.object(lecture, "recent_builds", return_value=builds) as recents:
        texte = lecture.builds()
    recents.assert_called_once_with("JOB_NUIT", 5)          # job en double compté une fois
    assert "#12 EN COURS" in texte and "#11 SUCCESS" in texte and "10 min" in texte
    with pytest.raises(lecture.LectureErreur):
        lecture.builds("AUTRE_JOB")


def test_cucumber(monkeypatch):
    monkeypatch.setattr(lecture.assistant, "known_clients", lambda: ["_standard", "client-a", "client-b"])
    monkeypatch.setattr(lecture.verrou, "holder", lambda c: {"pid": 42, "debut": "2026-11-19T09:00:00"}
                        if c == "client-a" else {})
    texte = lecture.cucumber()
    assert "client-a : génération en cours (processus 42" in texte
    assert "client-b : aucune génération en cours" in texte and "_standard" not in texte


def test_patchs_livres(tmp_path):
    registre = str(tmp_path / "livraisons.jsonl")
    assert "Aucun patch livré ces 7 derniers jours" in lecture.patchs(7, registre)
    maintenant = datetime.now()
    livraisons.noter("ODA-1", ["ODA-1-std.patch"], {"ODA-1-std.patch"}, quand=maintenant - timedelta(hours=2),
                     chemin=registre)
    livraisons.noter("ODA-2", ["ODA-2-std.patch", "ODA-2-client-x.patch"], {"ODA-2-std.patch"},
                     quand=maintenant - timedelta(hours=1), chemin=registre)
    livraisons.noter("ODA-3", ["ODA-3-std.patch"], set(), "Jira illisible (Timeout)", chemin=registre)
    livraisons.noter("ODA-4", ["ODA-4-std.patch"], {"ODA-4-std.patch"}, quand=maintenant - timedelta(days=20),
                     chemin=registre)
    lignes = lecture.patchs(7, registre).splitlines()
    assert lignes[0] == "Patchs livrés ces 7 derniers jours (agent Scrum Master) :"
    assert lignes[1].startswith("- ODA-3 (livré le ") and lignes[1].endswith(
        ": ODA-3-std.patch — pièce jointe non vérifiée (Jira illisible (Timeout))")
    assert lignes[2].endswith(": ODA-2-std.patch, ODA-2-client-x.patch — NON joint dans Jira : ODA-2-client-x.patch")
    debut = (maintenant - timedelta(hours=2)).strftime("%Y-%m-%d %H:%M")
    assert lignes[3] == f"- ODA-1 (livré le {debut}) : ODA-1-std.patch — en pièce jointe Jira"
    assert len(lignes) == 4                                               # ODA-4 hors fenêtre
    assert "ODA-4" in lecture.patchs(30, registre)
    assert "ces 90 derniers jours" in lecture.patchs(1000, registre)      # borné
    with pytest.raises(lecture.LectureErreur):
        lecture.patchs("beaucoup", registre)


def test_patchs_outil_mcp(monkeypatch, tmp_path):
    """L'outil est exposé par le serveur MCP et lit le registre (sans passer par un client stdio)."""
    pytest.importorskip("mcp.server.mcpserver")
    monkeypatch.chdir(os.getcwd())                                   # mcp_lecture change de dossier à l'import
    monkeypatch.setattr("dotenv.load_dotenv", lambda *a, **k: False)  # et charge le .env local : neutralisé
    import mcp_lecture
    registre = str(tmp_path / "livraisons.jsonl")
    livraisons.noter("ODA-1", ["ODA-1-std.patch"], {"ODA-1-std.patch"}, chemin=registre)
    monkeypatch.setattr(livraisons, "FICHIER", registre)
    serveur = mcp_lecture.creer_serveur()
    outils = {o.name: o for o in asyncio.run(serveur.list_tools())}
    assert "patchs" in outils and "jours" in json.dumps(outils["patchs"].input_schema)
    resultat = asyncio.run(serveur.call_tool("patchs", {"jours": 3}))
    assert "ODA-1 (livré le" in json.dumps(resultat, default=str, ensure_ascii=False)


def test_aucune_ecriture():
    """Le module ne fait que des lectures : aucun appel HTTP autre que GET, aucune fonction d'écriture de l'agent."""
    for fichier in ("tools/lecture_manager.py", "mcp_lecture.py"):
        source = open(os.path.join(lecture.WORKSPACE_DIR, fichier), encoding="utf-8").read()
        assert not re.search(r"requests\.(post|put|delete|patch)|trigger_|publish|validate\(|save_draft|"
                             r"record_publication|acquire\(|release\(|write_env|open\([^)]*['\"][wa]|"
                             r"livraisons\.noter", source), fichier
