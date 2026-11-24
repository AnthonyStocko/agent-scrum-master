"""Inventaire des spécifiques en tableau (déplacé de test_knowledge_tools d'agent-socle, qui teste share_knowledge)."""
import importlib
import io
import sys


def test_inventory_markdown_table():
    saved = sys.stdout, sys.stderr
    sys.stdout, sys.stderr = io.StringIO(), io.StringIO()
    try:
        main = importlib.import_module("main")
    finally:
        sys.stdout, sys.stderr = saved
    specifics = {"S1": {"id": "S1", "libelle": "depot | dossier", "type": "java", "couverture": "partiel"}}
    assert "| S1 | depot / dossier | java | partiel |" in main.inventory_markdown("clientfictif", specifics)
