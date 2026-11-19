"""Mémoire par spécifique entre générations (tools/cucumber_spec_memory.py)."""
import json
import os

import pytest

from tools import cucumber_spec_memory as sm
from tools import cucumber_tools

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FEATURE = "src/test/resources/cucumber/features/clientx/Stock.feature"
SC1, SC2 = f"{FEATURE}::Réception", f"{FEATURE}::Préparation"


@pytest.fixture
def cache(tmp_path, monkeypatch):
    monkeypatch.setattr(cucumber_tools, "CACHE_ROOT", str(tmp_path))
    monkeypatch.delenv("CUCUMBER_SPEC_MEMORY", raising=False)
    return tmp_path


def _lot(**extra):
    lot = {
        "numero": 2, "ids": ["sp-1", "sp-2"], "fin": "2026-11-19T10:00:00",
        "tags": {"@agent_clientx_2_1": SC1, "@agent_clientx_2_2": SC2},
        "indicateurs": {"scenarios": [{"fichier": FEATURE, "titre": "Réception", "spec": "sp-1"},
                                      {"fichier": FEATURE, "titre": "Préparation", "spec": "sp-2"}]},
        "execution": {"scenarios": {
            "@agent_clientx_2_1": {"id": SC1, "statut": "invalide", "motif": "Index 0 out of bounds"},
            "@agent_clientx_2_2": {"id": SC2, "statut": "pertinent", "motif": ""}}},
        "passation": {
            "@agent_clientx_2_1": [
                {"etape": "correction 1", "causes": [{"cause": "emplacement absent", "correction": "emplacement A",
                                                      "prouvee": False}],
                 "hypotheses": ["statut du stock"], "resultat": "même échec : hypothèse réfutée"},
                {"etape": "correction 2", "causes": [{"cause": "lot bloqué", "correction": "lot libre",
                                                      "prouvee": False}],
                 "hypotheses": ["paramètre du site"], "resultat": "nouvel échec : autre"}],
            "@agent_clientx_2_2": [
                {"etape": "correction 1", "causes": [{"cause": "quantité trop forte", "correction": "quantité 1",
                                                      "prouvee": True}], "resultat": "vert"}]},
        "correction": {"restantes_detail": [
            {"tag": "@agent_clientx_2_1", "nature": "validation_humaine", "cause": "code UM imposé",
             "valeur": {"step": "CONTENT- Le stock suivant", "avant": "UM-1", "apres": "UM-2"}},
            {"tag": "@agent_clientx_2_2", "nature": "a_verifier", "cause": "écran suivant non exécuté"}]},
    }
    lot.update(extra)
    return lot


def test_record_and_block_per_specific(cache):
    outcomes = {"sp-1": {"type": "test_ecrit", "message": "1 scénario"}, "sp-2": {"type": "test_ecrit"}}
    assert sm.record_lot("clientx", _lot(), outcomes, {"sp-1": "Réception spécifique", "sp-2": "Préparation"}) == \
        ["sp-1", "sp-2"]
    data = sm.load("clientx")["specifiques"]
    one, two = data["sp-1"]["generations"][0], data["sp-2"]["generations"][0]
    assert one["verts"] == 0 and one["en_echec"] == 1 and two["verts"] == 1
    assert one["pistes_ecartees"] == ["emplacement absent (corrigé : emplacement A, même échec ensuite)"]
    # Hypothèses de la dernière tentative non conclue seulement ; celles d'une tentative réfutée tombent
    assert one["hypotheses"] == ["paramètre du site"]
    assert "Réception : invalide — Index 0 out of bounds" in one["causes_ouvertes"]
    assert one["decisions_en_attente"] == ["code UM imposé — CONTENT- Le stock suivant « UM-1 » → « UM-2 »"]
    # Rien de sp-1 chez sp-2, et inversement
    assert two["ce_qui_a_marche"] == ["quantité trop forte → quantité 1"] and two["hypotheses"] == \
        ["écran suivant non exécuté"] and not two["decisions_en_attente"] and not two["pistes_ecartees"]
    text = sm.block("clientx", ["sp-1"], {"sp-1": {"total": 12.5}})
    assert "MÉMOIRE DES GÉNÉRATIONS PRÉCÉDENTES" in text and "Réception spécifique (sp-1)" in text
    assert "coût cumulé 12.50 $" in text and "ne la retente pas" in text
    assert "emplacement absent" in text and "UM-2" in text and "quantité trop forte" not in text
    assert sm.block("clientx", ["sp-inconnu"]) == ""


def test_generations_are_bounded_and_older_pistes_kept(cache):
    for i in range(sm.MAX_GENERATIONS + 2):
        lot = _lot(ids=["sp-1"], numero=i)
        if i:
            lot["passation"] = {}
        sm.record_lot("clientx", lot)
    generations = sm.load("clientx")["specifiques"]["sp-1"]["generations"]
    assert len(generations) == sm.MAX_GENERATIONS
    # Une piste écartée par une génération plus ancienne (encore gardée) reste dans le bloc ; ici la 1re est sortie
    assert "emplacement absent" not in sm.block("clientx", ["sp-1"])
    sm.record_lot("clientx", _lot(ids=["sp-1"]))
    sm.record_lot("clientx", {**_lot(ids=["sp-1"]), "passation": {}})
    assert "emplacement absent" in sm.block("clientx", ["sp-1"])


def test_single_specific_lot_takes_unmapped_tags_and_a_revoir(cache):
    lot = _lot(ids=["sp-1"], indicateurs={}, passation={}, correction={},
               a_revoir=[{"id": SC1, "titre": "Réception", "cause": "environnement", "prouvee": True,
                          "preuve": "Connection refused", "reactivation": "réactiver quand le service répond"}],
               decisions_en_attente=[SC2], arret=["seuil de désactivation dépassé"], erreur="code 1")
    sm.record_lot("clientx", lot)
    entry = sm.load("clientx")["specifiques"]["sp-1"]["generations"][0]
    assert entry["en_echec"] == 1 and entry["verts"] == 1
    assert any("environnement (prouvé : Connection refused) — réactiver" in c for c in entry["causes_ouvertes"])
    # Scénario désactivé : pas recompté comme échec sans cause
    assert not any(c.startswith("Réception : invalide") for c in entry["causes_ouvertes"])
    assert "lot en erreur : code 1" in entry["causes_ouvertes"]
    assert "Préparation : modification en attente de décision humaine" in entry["decisions_en_attente"]
    assert "arrêt du lot : seuil de désactivation dépassé" in entry["decisions_en_attente"]


def test_block_is_bounded(cache, monkeypatch):
    monkeypatch.setattr(sm, "MAX_CHARS", 1200)
    long = "x" * 300
    lot = _lot(ids=["sp-1"], passation={}, correction={"restantes_detail": [
        {"tag": "@agent_clientx_2_1", "nature": "a_verifier", "cause": f"{long} {i}"} for i in range(10)]})
    for _ in range(3):
        sm.record_lot("clientx", lot)
    assert len(sm.block("clientx", ["sp-1"])) < 1200 + 200


def test_disabled_and_unreadable_file(cache, monkeypatch):
    with open(sm.path("clientx"), "w", encoding="utf-8") as f:
        f.write("{illisible")
    assert sm.load("clientx")["specifiques"] == {}
    monkeypatch.setenv("CUCUMBER_SPEC_MEMORY", "0")
    assert sm.record_lot("clientx", _lot()) == [] and sm.block("clientx", ["sp-1"]) == ""


def test_record_never_raises(cache, monkeypatch, capsys):
    def boom(*a, **k):
        raise OSError("disque plein")
    monkeypatch.setattr(sm, "save", boom)
    assert sm.record_lot("clientx", _lot()) == []
    assert "non mise à jour" in capsys.readouterr().out


def test_lot_prompt_has_memory_slot():
    with open(os.path.join(ROOT, "prompts", "cucumber_client_lot.md"), encoding="utf-8") as f:
        assert "${memoire}" in f.read()
