"""
Nettoyage des données des tests générés, pour tous les clients : chaque test laisse l'environnement dans l'état où il
l'a trouvé, même exécuté sans l'agent (Jenkins, poste d'un développeur).

- Installation dans le projet de tests du client : plugin cucumber.nettoyage.JournalEtapes (step en cours, Standard
  compris) déclaré dans le RunnerTest, hook cucumber.nettoyage.NettoyageHooks (journal des créations et des valeurs
  d'origine lues avant modification ; @After qui rejoue en ordre inverse les steps de suppression, d'annulation ou de
  clôture du Standard et remet les valeurs d'origine ; tolérant, bilan « [NETTOYAGE] » en console), catalogue
  cucumber/nettoyage/catalogue.json (agent) et catalogue_projet.json (steps propres au projet, ajoutés par un lot).
- Contrôle déterministe avant livraison : chaque step de création ou de modification utilisé par un test généré a un
  nettoyage (catalogue), et chaque step de nettoyage existe dans la bibliothèque du Standard ou le projet ; sinon,
  erreur de lot.
Source unique : tools/data/cucumber_nettoyage (ne jamais modifier les copies du projet du client).
"""
import json
import os
import re

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA_DIR = os.path.join(ROOT, "tools", "data", "cucumber_nettoyage")
JAVA_FILES = ("JournalEtapes.java", "NettoyageHooks.java")
JAVA_DIR = "src/test/java/cucumber/nettoyage"
RESOURCE_DIR = "src/test/resources/cucumber/nettoyage"
PROJECT_CATALOGUE = "catalogue_projet.json"
PLUGIN = "cucumber.nettoyage.JournalEtapes"
RUNNER_GLOB = "src/test/java"
MODES = ("suppression", "annulation", "cloture", "restauration", "fichiers", "couvert", "lecture")
LESSON = ("un test laisse l'environnement dans l'état où il l'a trouvé : chaque step de création ou de modification "
          "a son nettoyage (catalogue cucumber/nettoyage), rejoué par le hook @After du projet")
_STEP = re.compile(r"^\s*(?:Given|When|Then|And|But|\*|Soit|Quand|Alors|Et|Mais)\s+(.*?)\s*$")
_TAGS = re.compile(r"^\s*@")
# Steps qui créent ou modifient des données (convention de nommage de la bibliothèque) ; les lectures et contrôles
# (Initialisation, <X>, Contrôle, Vérification) n'en font pas partie
_MUTATING = re.compile(r"(Intégration|implantations suivantes|stock suivant|\bLes? [^|]* suivante?s?\b|Création|Créer|"
                       r"Modification|Modifier|Ajout|Ajouter|Paramétrage|Configuration|Campagne|Lancement|Lancer|"
                       r"Mise en [Pp]réparation|Préparation des|Précoliser|Clôture|Clôturer|Validation|Valider|"
                       r"Suppression|Supprimer|Annulation|Annuler|Affectation|Affecter|Entrer sur|Ouvrir une interaction)")
_READING = re.compile(r"^[A-Z]+- (<X>|Initialisation|Contrôle|Vérification|Le process interactif affiche|"
                      r"Le champ personnalisé)")


def _read(path: str) -> str:
    try:
        with open(path, encoding="utf-8") as f:
            return f.read()
    except OSError:
        return ""


def catalogue(worktree: str = "") -> list:
    """Étapes du catalogue de l'agent, puis celles du projet (catalogue_projet.json du client)."""
    steps = list((json.loads(_read(os.path.join(DATA_DIR, "catalogue.json")) or "{}")).get("etapes") or [])
    if worktree:
        try:
            project = json.loads(_read(os.path.join(worktree, *RESOURCE_DIR.split("/"), PROJECT_CATALOGUE)) or "{}")
        except ValueError:
            project = {}
        steps += [s for s in project.get("etapes") or [] if isinstance(s, dict)]
    return steps


def _pattern(expression: str) -> re.Pattern:
    escaped = re.escape(expression)
    for token, regex in (("\\{string\\}", '"[^"]*"'), ("\\{int\\}", r"-?\d+"), ("\\{word\\}", r"\S+")):
        escaped = escaped.replace(token, regex)
    return re.compile(f"^{escaped}$")


def find(steps: list, text: str) -> dict:
    return next((s for s in steps if _pattern(s.get("step", "")).match(text.strip())), {})


# ---------------------------------------------------------------------------
# Installation dans le projet de tests du client
# ---------------------------------------------------------------------------

def _write_if_changed(path: str, text: str) -> bool:
    if _read(path) == text:
        return False
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        f.write(text)
    return True


def runner_files(worktree: str) -> list:
    out = []
    for base, _dirs, names in os.walk(os.path.join(worktree, *RUNNER_GLOB.split("/"))):
        for name in names:
            if name.endswith(".java"):
                path = os.path.join(base, name)
                if "@CucumberOptions" in _read(path):
                    out.append(path)
    return out


def register_plugin(text: str) -> str:
    """RunnerTest avec le plugin du journal déclaré (ajouté à « plugin = {…} », ou l'option créée)."""
    if PLUGIN in text:
        return text
    match = re.search(r"plugin\s*=\s*\{", text)
    if match:
        return text[:match.end()] + f'\n                "{PLUGIN}",' + text[match.end():]
    match = re.search(r"@CucumberOptions\s*\(", text)
    if not match:
        return text
    return text[:match.end()] + f'\n        plugin = {{"{PLUGIN}"}},' + text[match.end():]


def install(worktree: str) -> list:
    """Classes Java, catalogue et plugin installés ou mis à jour dans le projet ; fichiers modifiés (relatifs)."""
    changed = []
    for name in JAVA_FILES:
        rel = f"{JAVA_DIR}/{name}"
        if _write_if_changed(os.path.join(worktree, *rel.split("/")), _read(os.path.join(DATA_DIR, "java", name))):
            changed.append(rel)
    rel = f"{RESOURCE_DIR}/catalogue.json"
    if _write_if_changed(os.path.join(worktree, *rel.split("/")), _read(os.path.join(DATA_DIR, "catalogue.json"))):
        changed.append(rel)
    for path in runner_files(worktree):
        text = _read(path)
        updated = register_plugin(text)
        if updated != text:
            with open(path, "w", encoding="utf-8", newline="") as f:
                f.write(updated)
            changed.append(os.path.relpath(path, worktree).replace(os.sep, "/"))
    return changed


# ---------------------------------------------------------------------------
# Contrôle avant livraison
# ---------------------------------------------------------------------------

def feature_steps(text: str) -> list:
    """[(ligne, texte du step)] d'une feature, hors features d'initialisation (@INIT_…) et lignes commentées."""
    lines = (text or "").splitlines()
    if any(_TAGS.match(l) and "@INIT_" in l for l in lines[:5]):
        return []
    return [(n, m.group(1)) for n, l in enumerate(lines, 1) if not l.strip().startswith("#")
            for m in [_STEP.match(l)] if m]


def mutating(text: str) -> bool:
    return bool(_MUTATING.search(text)) and not _READING.match(text)


def library_methods(dirs: list) -> set:
    """{(classe, méthode)} des classes Java des sources données (bibliothèque du Standard, projet)."""
    out = set()
    for base_dir in dirs:
        root = os.path.join(base_dir, "src", "test", "java")
        for base, _dirs, names in os.walk(root):
            for name in names:
                if not name.endswith(".java"):
                    continue
                path = os.path.join(base, name)
                text = _read(path)
                package = re.search(r"^\s*package\s+([\w.]+)\s*;", text, re.M)
                cls = f"{package.group(1)}.{name[:-5]}" if package else name[:-5]
                for method in re.findall(r"public\s+void\s+(\w+)\s*\(\s*(?:final\s+)?DataTable\b", text):
                    out.add((cls, method))
    return out


def check_catalogue(steps: list, methods: set) -> list:
    """Problèmes du catalogue : mode inconnu, nettoyage absent, step de nettoyage inexistant dans les sources."""
    problems = []
    for step in steps:
        mode = step.get("mode")
        if mode not in MODES:
            problems.append(f"« {step.get('step')} » : mode « {mode} » inconnu ({', '.join(MODES)})")
            continue
        actions = list(step.get("nettoyage") or []) + [a for r in step.get("fichiers") or []
                                                         for a in r.get("nettoyage") or []]
        if mode in ("suppression", "annulation", "cloture") and not actions:
            problems.append(f"« {step.get('step')} » : aucun step de nettoyage")
        for action in actions:
            if methods and (action.get("classe"), action.get("methode")) not in methods:
                problems.append(f"« {step.get('step')} » : step de nettoyage « {action.get('step')} » introuvable "
                                f"({action.get('classe')}.{action.get('methode')})")
    return problems


def check_files(worktree: str, files: list, source_dirs: list = ()) -> list:
    """Erreurs de lot : step de création ou de modification sans nettoyage, catalogue du projet incohérent."""
    steps = catalogue(worktree)
    methods = library_methods([worktree] + [d for d in source_dirs if d])
    errors = [f"{RESOURCE_DIR}/{PROJECT_CATALOGUE} : {p}"
              for p in check_catalogue([s for s in steps if s not in catalogue()], methods)]
    for rel in files:
        if not rel.endswith(".feature"):
            continue
        for number, text in feature_steps(_read(os.path.join(worktree, *rel.split("/")))):
            if not find(steps, text) and mutating(text):
                errors.append(f"{rel}:{number} : step « {text} » qui crée ou modifie des données sans nettoyage "
                              f"connu — {LESSON}. Utilise un step déjà couvert, ou ajoute son nettoyage dans "
                              f"{RESOURCE_DIR}/{PROJECT_CATALOGUE} (même format que catalogue.json : step, objet, "
                              f"mode, nettoyage = steps existants de suppression / annulation / clôture avec leur "
                              f"classe et méthode).")
    return errors


PROMPT = f"""
NETTOYAGE DES DONNÉES (tests exécutables sans l'agent) :
- Règle : {LESSON}. Le nettoyage est automatique (plugin et hook déjà installés) : n'écris AUCUN step de suppression
  dans le scénario, et ne modifie jamais cucumber/nettoyage/*.java ni catalogue.json.
- N'utilise pour créer ou modifier des données que des steps du catalogue ; pour un autre step, ajoute son entrée dans
  {RESOURCE_DIR}/{PROJECT_CATALOGUE} (steps de suppression / annulation / clôture EXISTANTS, classe et méthode).
- Codes créés par le test : propres au test (préfixe du spécifique) ; une création doit pouvoir être rejouée si un
  nettoyage a échoué (un ordre annulé reste en base : jamais deux tests sur le même code d'ordre).
"""


# ---------------------------------------------------------------------------
# Preuve : relecture de la base après le second run (lecture seule)
# ---------------------------------------------------------------------------

FEATURES_ROOT = "src/test/resources/cucumber/features/"
FILES_ROOT = "src/test/resources/cucumber/files/"
_PLACEHOLDER = re.compile(r"\{(?:(fichier):)?([^{}]+)\}")


def _cells(line: str) -> list:
    return [c.strip() for c in line.strip().strip("|").split("|")]


def steps_with_tables(text: str) -> list:
    """[(ligne, texte, [lignes de la table en dict])] des steps d'une feature (commentaires ignorés)."""
    lines = (text or "").splitlines()
    out, index = [], 0
    while index < len(lines):
        match = _STEP.match(lines[index]) if not lines[index].strip().startswith("#") else None
        index += 1
        if not match:
            continue
        number, table = index, []
        while index < len(lines) and lines[index].strip().startswith(("|", "#")):
            if lines[index].strip().startswith("|"):
                table.append(_cells(lines[index]))
            index += 1
        rows = [dict(zip(table[0], r)) for r in table[1:]] if len(table) > 1 else []
        out.append((number, match.group(1), rows))
    return out


def file_values(path: str, rule: dict) -> dict:
    """{fichier:nom: valeur} d'un fichier intégré pour une règle du catalogue ; {} si le message n'y est pas."""
    from lxml import etree
    try:
        document = etree.parse(path)
    except (OSError, etree.XMLSyntaxError):
        return {}
    if not document.xpath(f"//*[local-name()='{rule['message']}']"):
        return {}
    out = {}
    for name, expression in (rule.get("valeurs") or {}).items():
        found = document.xpath(f"string({expression})")
        out[f"fichier:{name}"] = str(found).strip()
    return out


def _fill(template: str, values: dict) -> str:
    def value(match):
        key = f"fichier:{match.group(2)}" if match.group(1) else match.group(2)
        return str(values.get(key, "")).replace("'", "''")
    return _PLACEHOLDER.sub(value, template)


def created_objects(worktree: str, rel: str) -> list:
    """
    Objets créés ou modifiés par une feature, avec leur requête de relecture : [{objet, mode, cle, sql, propre,
    tolere, comparaison}] (valeurs des tables et des fichiers GS1 intégrés).
    """
    steps = catalogue(worktree)
    text = _read(os.path.join(worktree, *rel.split("/")))
    directory = rel[len(FEATURES_ROOT):].rsplit("/", 1)[0] if rel.startswith(FEATURES_ROOT) else ""
    out, document = [], ""
    for _number, step_text, rows in steps_with_tables(text):
        entry = find(steps, step_text)
        quoted = re.findall(r'"([^"]*)"', step_text)
        if entry.get("objet") == "document" and entry.get("mode") == "lecture" and quoted:
            document = quoted[0]
        check = entry.get("verification")
        if entry.get("mode") == "fichiers":
            for row in rows:
                path = os.path.join(worktree, *FILES_ROOT.split("/"), *directory.split("/"), row.get("fileName", ""))
                for rule in entry.get("fichiers") or []:
                    values = file_values(path, rule)
                    if values and rule.get("verification"):
                        out.append(_object(rule.get("objet"), rule.get("mode"), rule["verification"], values))
        elif check and entry.get("mode") == "restauration":
            out.append(_object(entry.get("objet"), "restauration", check, {"document": document}))
        elif check:
            for row in rows:
                out.append(_object(entry.get("objet"), entry.get("mode"), check, row))
    unique = {}
    for item in out:
        unique.setdefault(item["sql"], item)
    return list(unique.values())


def _object(objet: str, mode: str, check: dict, values: dict) -> dict:
    key = ", ".join(str(v) for k, v in values.items() if v and not k.startswith("_"))[:120]
    return {"objet": objet, "mode": mode, "cle": key, "sql": _fill(check["sql"], values),
            "propre": list(check.get("propre") or []), "tolere": list(check.get("tolere") or []),
            "comparaison": check.get("comparaison", "")}


def snapshot(objects: list, schema: str, query) -> dict:
    """Valeurs avant le premier run des objets à comparer (restauration) : {sql: valeur}."""
    out = {}
    for item in objects:
        if item["comparaison"] == "avant":
            rows = query(item["sql"], schema)
            out[item["sql"]] = str(rows[0].get("code", "")) if rows else ""
    return out


def leftovers(objects: list, schema: str, query, before: dict = None) -> dict:
    """
    Relecture après le second run : {propres, toleres, restes, illisibles} (listes de libellés). Reste : objet créé
    encore présent, ou valeur modifiée différente de celle d'avant le premier run.
    """
    result = {"propres": [], "toleres": [], "restes": [], "illisibles": []}
    for item in objects:
        label = f"{item['objet']} {item['cle']}".strip()
        try:
            rows = query(item["sql"], schema)
        except Exception as e:  # noqa: BLE001
            result["illisibles"].append(f"{label} ({str(e)[:120]})")
            continue
        value = str(rows[0].get("code", "")).strip() if rows else ""
        value = "" if value == "NULL" else value
        if item["comparaison"] == "avant":
            expected = (before or {}).get(item["sql"])
            if expected is None:
                result["illisibles"].append(f"{label} (valeur d'avant le premier run non relevée)")
            elif value == expected:
                result["propres"].append(f"{label} : {value or 'vide'} (comme avant)")
            else:
                result["restes"].append(f"{label} : « {value} » au lieu de « {expected} » (non restauré)")
        elif value in item["propre"]:
            result["propres"].append(f"{label} : supprimé")
        elif value in item["tolere"]:
            result["toleres"].append(f"{label} : {value} ({item['mode']}, toléré)")
        else:
            result["restes"].append(f"{label} : encore présent ({value})")
    return result
