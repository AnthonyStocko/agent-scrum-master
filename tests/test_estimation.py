"""Chiffrage de l'analyse d'un ticket : section, cohérence, publication à la validation, calibrage, épopées."""
import pytest

from tools import analysis_publication, estimation, memory_tools

ANALYSIS = """# Cadrage PRJ-12

## 🛠️ Partie 2 : Analyse Technique
- **Détail des Modifications Techniques** : ajout d'un contrôle.

## 💰 Chiffrage

Unité : jours-homme.

| Activité | Basse | Probable | Haute | Éléments justificatifs |
|---|---|---|---|---|
| Développement Java — wms-preparation-core | 1 | 1,5 | 3 | AssignNeedAction.java, 1 classe de test |
| Personnalisation Studio | 0,5 | 0,5 | 1 | processus palletize-by-preparationunit |
| Tests (unitaires, Cucumber) | 0,5 | 1 | 1,5 | 2 scénarios Cucumber |
| **Total** | **9** | **9** | **9** | |

**Risques et hypothèses** : zone de code mal connue (cubing).

**Confiance** : moyen — spécification claire, cubing peu connu.

**Calibrage sur l'historique** : aucun historique exploitable, fourchette élargie.
"""


@pytest.fixture(autouse=True)
def enabled(monkeypatch, tmp_path):
    monkeypatch.setenv("ESTIMATION_ENABLED", "1")
    monkeypatch.setattr(memory_tools, "MEMORY_DIR", str(tmp_path / "memory"))
    monkeypatch.setattr(analysis_publication, "PUBLICATION_DIR", str(tmp_path / "publication"))
    monkeypatch.chdir(tmp_path)


def test_section_is_read_and_the_total_is_always_the_sum_of_activities():
    parsed = estimation.parse(ANALYSIS)
    assert parsed["present"] and parsed["unite"] == "jours-homme" and parsed["confiance"] == "moyen"
    assert [r["probable"] for r in parsed["lignes"]] == [1.5, 0.5, 1.0]
    config = estimation.settings("PRJ")
    assert "total différent de la somme des activités" in estimation.check(ANALYSIS, config)
    fixed, changed = estimation.normalize(ANALYSIS)
    assert changed and "| **Total** | **2** | **3** | **5,5** |" in fixed
    assert estimation.check(fixed, config) == []
    # Total absent : ajouté ; fourchette incohérente signalée
    without = ANALYSIS.replace("| **Total** | **9** | **9** | **9** | |\n", "")
    assert estimation.totals(estimation.parse(estimation.normalize(without)[0])) == {"basse": 2.0, "probable": 3.0,
                                                                                    "haute": 5.5}
    broken = fixed.replace("| 0,5 | 1 | 1,5 | 2 scénarios", "| 2 | 1 | 1,5 | 2 scénarios")
    assert any("basse ≤ probable ≤ haute" in p for p in estimation.check(broken, config))
    assert estimation.check("# Cadrage\n\n## Partie 2\n", config) == ["section « Chiffrage » absente"]
    # Unité absente de la section : écrite sous le titre (unité du projet), jamais signalée à tort
    no_unit = fixed.replace("Unité : jours-homme.\n", "")
    assert "unité attendue : jours-homme" in estimation.check(no_unit, config)
    with_unit, changed = estimation.normalize(no_unit, "jours-homme")
    assert changed and estimation.check(with_unit, config) == []


def test_project_settings_unit_field_and_activities(monkeypatch):
    config = estimation.save_settings("PRJ", {"unite": "points", "champ": "customfield_10016",
                                              "activites": ["dev_java", "tests"]})
    assert config["unite"] == "points" and config["champ"] == "customfield_10016"
    assert estimation.activity_labels(config) == ["Développement Java", "Tests (unitaires, Cucumber)"]
    block = estimation.prompt_block("PRJ", [])
    assert "points" in block and "Personnalisation Studio" not in block
    assert estimation.settings("AUTRE")["unite"] == "jours-homme"
    parsed = estimation.parse(estimation.normalize(ANALYSIS)[0])
    assert estimation.jira_update(parsed, config) == {"customfield_10016": 3.0}
    assert estimation.jira_update(parsed, estimation.settings("AUTRE")) == {
        "timetracking": {"originalEstimate": "24h"}}


def test_estimate_is_written_to_jira_only_at_validation():
    writes = []
    text = estimation.normalize(ANALYSIS)[0]
    analysis_publication.save_draft("PRJ-12", text)        # brouillon / révision : rien n'est écrit
    assert writes == []
    state = analysis_publication.validate("PRJ-12", text, publisher=lambda *a, **k: None,
                                          estimate_put=lambda key, fields: writes.append((key, fields)))
    assert writes == [("PRJ-12", {"timetracking": {"originalEstimate": "24h"}})]
    assert state["chiffrage"]["ecrit"] and "3 jours-homme" in state["libelle"]
    assert memory_tools.load_ticket_memory("PRJ-12")["chiffrage"]["probable"] == 3.0


def test_calibration_without_history_is_reported_and_widens_the_range():
    block = estimation.prompt_block("PRJ", [])
    assert "AUCUN ticket similaire exploitable" in block and "ÉLARGIS la fourchette" in block
    history = estimation.jira_history("PRJ", "Palettisation automatique des unités", "PRJ-12",
                                      estimation.settings("PRJ"),
                                      search=lambda jql, fields: [{"key": "PRJ-3", "fields": {
                                          "summary": "Palettisation", "timespent": 4 * 3600 * 8,
                                          "timeoriginalestimate": 2 * 3600 * 8}}])
    assert history[0]["reel"] == 4.0 and history[0]["estimation"] == 2.0
    assert "réellement passé 4.0 jours-homme" in estimation.prompt_block("PRJ", history)

    def unreachable(jql, fields):
        raise RuntimeError("Jira injoignable")
    assert estimation.jira_history("PRJ", "Palettisation automatique", "PRJ-12", estimation.settings("PRJ"),
                                   search=unreachable) == []


def test_actual_time_after_delivery_is_compared_and_remembered():
    text = estimation.normalize(ANALYSIS)[0]
    estimation.record_estimate("PRJ-12", "PRJ", estimation.parse(text), estimation.settings("PRJ"))
    done = estimation.record_actuals("PRJ", fetch=lambda key: {"done": True, "timespent": 4 * 8 * 3600})
    assert done == [{"cle": "PRJ-12", "valeur": 4.0, "unite": "jours-homme", "date": done[0]["date"], "ecart": 1.0,
                     "chiffrage": 3.0}]
    assert memory_tools.load_ticket_memory("PRJ-12")["reel"]["ecart"] == 1.0
    # Déjà enregistré : pas relu
    assert estimation.record_actuals("PRJ", fetch=lambda key: {"done": True, "timespent": 1}) == []


def test_epic_estimate_per_child_task_and_total():
    text = estimation.normalize(ANALYSIS)[0]
    summary = estimation.epic_summary([("PRJ-13 — tâche 1", text), ("Tâche 2", text), ("Tâche 3", "sans chiffrage"),
                                       ("PRJ-14", "")], "PRJ")
    assert "| PRJ-13 — tâche 1 | 2 | 3 | 5,5 |" in summary and "| Tâche 3 | chiffrage absent |" in summary
    assert "| PRJ-14 | analyse manquante | analyse manquante | analyse manquante |" in summary
    assert "| **Total** | **4** | **6** | **11** |" in summary


def test_analysis_attached_to_an_existing_task_is_written_to_its_own_file_and_never_empty():
    from tools import epic_tools
    with open("EPIC-1-tache-1.md", "w", encoding="utf-8") as f:
        f.write(ANALYSIS)
    open("PRJ-12-cadrage.md", "w", encoding="utf-8").close()        # fichier vide laissé par l'écran
    manifest = {"epic": "EPIC-1", "tasks": [{"summary": "s", "file": "EPIC-1-tache-1.md", "key": "PRJ-12"},
                                            {"summary": "t", "file": "EPIC-1-tache-2.md", "key": ""}]}
    # Avant rattachement : le fichier vide de la tâche n'est pas pris pour son analyse
    assert epic_tools.task_analysis(manifest["tasks"][0]) == ("EPIC-1-tache-1.md", ANALYSIS)
    assert epic_tools.attach_existing_analyses(manifest) == ["PRJ-12"]
    assert manifest["tasks"][0]["file"] == "PRJ-12-cadrage.md"
    assert epic_tools.task_analysis(manifest["tasks"][0]) == ("PRJ-12-cadrage.md", ANALYSIS)
    # Tâche sans analyse : signalée (boutons de validation bloqués dans l'écran)
    assert [t["file"] for t in epic_tools.missing_analyses(manifest)] == ["EPIC-1-tache-2.md"]
