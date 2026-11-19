"""Prévention avant écriture (tools/cucumber_prevention.py) et contrôle des colonnes par l'agent."""
import os

import pytest

from tests.conftest import git, write
from tools import cucumber_compile, cucumber_prevention as cp, cucumber_tools as ct, cucumber_verify as cv

REL = "src/test/resources/cucumber/features/clientx/WMS/A.feature"
STEP = "PREP- Contrôle des ordres de préparation"
COLUMNS = {STEP: ["code", "codeShipment", "status.id"]}


@pytest.fixture
def cache(tmp_path, monkeypatch):
    monkeypatch.setattr(ct, "CACHE_ROOT", str(tmp_path / "cache"))
    monkeypatch.delenv("CUCUMBER_PREVENTION", raising=False)
    return tmp_path


def _column_error(line: int, column: str) -> str:
    return (f"{REL}:{line} colonne « {column} » jamais utilisée au Standard pour le step « {STEP} » : colonne la plus "
            f"proche « codeShipment » (syntaxe du Standard actuel)")


def test_kind_ignores_path_line_and_values():
    a = cp.kind(_column_error(57, "customString1"))
    b = cp.kind(_column_error(12, "autreColonne"))
    assert a == b and "src/test" not in a and "customString1" not in a
    assert cp.kind("src/a.feature : tag @X : la convention du client est @x") != a
    assert cp.message(_column_error(57, "customString1")).startswith("colonne « customString1 »")


def test_record_and_block(cache):
    assert cp.block("clientx") == ""
    cp.record("clientx", [_column_error(57, "customString1"), _column_error(58, "customString2")], 1)
    # Une seule occurrence par lot et par type ; sous le seuil : pas encore de consigne
    assert cp.load("clientx")["types"][cp.kind(_column_error(1, "x"))]["nombre"] == 1
    assert cp.block("clientx", COLUMNS) == ""
    cp.record("clientx", [_column_error(9, "customString1"), "compilation : cannot find symbol Foo"], 2)
    text = cp.block("clientx", COLUMNS)
    assert "CONTRÔLES AUTOMATIQUES" in text and "2 fois" in text and "customString1" in text
    assert f"colonnes acceptées par le Standard pour « {STEP} » : code, codeShipment, status.id" in text
    assert "cannot find symbol" not in text          # 1 seule fois
    entry = cp.load("clientx")["types"][cp.kind(_column_error(1, "x"))]
    assert entry["steps"] == [STEP] and len(entry["exemples"]) == 1 and entry["lot"] == 2


def test_block_bounded_and_sorted(cache, monkeypatch):
    for lot in range(3):
        cp.record("clientx", [f"src/x/F{i}.feature:1 règle numéro {'x' * i} « v » non respectée " + "y" * 300
                              for i in range(12)] + [_column_error(1, "c")] * 1, lot)
    cp.record("clientx", [_column_error(2, "c")], 4)
    monkeypatch.setattr(cp, "MAX_CHARS", 900)
    text = cp.block("clientx")
    assert len(text) <= 902 and text.splitlines()[2].startswith("- 4 fois")
    assert text.count("\n- ") <= cp.MAX_RULES


def test_disabled_and_never_raises(cache, monkeypatch, capsys):
    monkeypatch.setattr(cp, "_write", lambda *a: (_ for _ in ()).throw(OSError("disque plein")))
    assert cp.record("clientx", ["x"]) == 0 and "non mis à jour" in capsys.readouterr().out
    monkeypatch.setenv("CUCUMBER_PREVENTION", "0")
    assert cp.record("clientx", ["x"]) == 0 and cp.block("clientx") == ""


def test_agent_column_errors_only_on_lot_changes(cache, repo):
    path = os.path.join(repo, *REL.split("/"))
    write(path, f"Feature: A\n  Scenario: S\n    Then {STEP}\n      | code | customString1 |\n      | OP-1 | X |\n")
    git(repo, "add", "-A")
    git(repo, "commit", "-qm", "lot précédent")
    assert cp.agent_column_errors("clientx", repo) == []              # pas de colonnes_std.json
    cp.write_columns("clientx", COLUMNS)
    assert cp.agent_column_errors("clientx", repo) == []              # déjà commité : hors du lot en cours
    new = REL.replace("A.feature", "B.feature")
    write(os.path.join(repo, *new.split("/")),
          f"Feature: B\n  Scenario: S\n    Then {STEP}\n      | code | customString1 |\n      | OP-2 | Y |\n")
    errors = cp.agent_column_errors("clientx", repo)
    assert len(errors) == 1 and errors[0].startswith(f"{new}:3 colonne « customString1 »")
    # Même contrôle que celui fait après le lot
    assert errors == cv.check_table_columns(repo, "HEAD", [new], {"colonnes_par_step": COLUMNS})


def test_agent_compile_tool_reports_column_errors(cache, repo, monkeypatch, capsys):
    new = REL.replace("A.feature", "B.feature")
    write(os.path.join(repo, "README"), "x")
    git(repo, "add", "-A")
    git(repo, "commit", "-qm", "base")
    write(os.path.join(repo, *new.split("/")),
          f"Feature: B\n  Scenario: S\n    Then {STEP}\n      | code | customString1 |\n      | OP-2 | Y |\n")
    cucumber_compile.write_context("clientx", repo, "", "mvn test-compile")
    cp.write_columns("clientx", COLUMNS)
    monkeypatch.setattr(ct, "build_dir", lambda *a: repo)
    monkeypatch.setattr(ct, "verify_build", lambda build, cmd: {"ok": True, "erreurs": [], "environnement": False})
    monkeypatch.setattr(cucumber_compile, "api_errors", lambda worktree: ("1.0", []))
    assert cucumber_compile.main(["clientx"]) == 1
    out = capsys.readouterr().out
    assert "CONTRÔLE DES FEATURES KO" in out and "customString1" in out
    # Colonne corrigée : OK
    write(os.path.join(repo, *new.split("/")),
          f"Feature: B\n  Scenario: S\n    Then {STEP}\n      | code | codeShipment |\n      | OP-2 | Y |\n")
    assert cucumber_compile.main(["clientx"]) == 0 and "COMPILATION OK" in capsys.readouterr().out


def test_lot_prompt_has_prevention_slot():
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    with open(os.path.join(root, "prompts", "cucumber_client_lot.md"), encoding="utf-8") as f:
        assert "${prevention}" in f.read()
