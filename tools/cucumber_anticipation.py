"""
Analyse statique d'anticipation (0 token, sans run), à la version testée, avant toute génération — tous clients.

Point 1.a : Studio. Pour chaque processus personnalisé déployé dans la base des tests (kis_custom_process_version,
dernière version de chaque composant) :
- schéma : XML bien formé, chaque transition vers un nœud existant (ou $end) ;
- statut du composant : autre que VALID (DEPENDENCY_MISSING…) = composant non exécutable ;
- chaque action, processus ou composant référencé (<action-ref key>, <process-ref key>) existe à la version testée ;
- chaque variable passée en entrée (<input to="…">) est déclarée par le composant appelé.

« Version testée » : celle que l'environnement exécute, résolue comme lui —
1. processus personnalisé déployé (même table) ;
2. FORK du client : composant listé « runtime:<groupe du client>/<composant>@<version> » dans le bundle.list de son
   application, lu dans ses dépôts à la référence de la version (classe @Action : champs @In / @InOut ; processus :
   <variable scope="in|inout">) ;
3. Standard à la version importée par ce bundle.list (« import:<groupe>/<contrib>@26.22-SNAPSHOT » → 26.22), lu dans
   Bitbucket.
Un composant introuvable alors que ses sources sont lisibles est « absent de la version » ; une source illisible
(Bitbucket injoignable, clé hors convention, dépôt inconnu) donne « non vérifié », jamais une anomalie.

Chaque anomalie : {anomalie, composant, fichier, ligne, impact, action, classe, preuve} ; classe = « défaut produit
probable » (processus ou composant du client incohérent avec la version) ou « risque d'environnement » (composant
non déployé, version de l'environnement).
"""
import argparse
import os
import re
import sys
import xml.etree.ElementTree as ET

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

VALID_STATUS = "VALID"
ANALYSIS_VERSION = 3   # changée quand les contrôles changent : analyse recalculée
PRODUCT = "défaut produit probable"
ENVIRONMENT = "risque d'environnement"
_KIND = re.compile(r"^(?P<prefix>.+?)-(?P<kind>action|process)-(?P<rest>.+)$")
_IN_FIELD = re.compile(r"@(In|InOut)\b(?:\s*\([^)]*\))?\s*(?:@\w+(?:\s*\([^)]*\))?\s*)*"
                       r"(?:(?:private|protected|public|final|transient)\s+)*[\w.<>\[\],?\s]+?\s+(\w+)\s*[;=]")
_BUNDLE_LINE = re.compile(r"^\s*(runtime|import)\s*:\s*([\w.-]+)/([\w.-]+)@([\w.-]+)", re.M)
_END = {"$end", "$fail", "$error", "$super", ""}


def _local(tag) -> str:
    return tag.rsplit("}", 1)[-1] if isinstance(tag, str) else ""


def _line_of(text: str, needle: str) -> int:
    index = (text or "").find(needle)
    return (text or "").count("\n", 0, index) + 1 if index >= 0 else 0


# ---------------------------------------------------------------------------
# Définitions
# ---------------------------------------------------------------------------

def parse_process(xml_text: str) -> dict:
    """
    {variables: {nom: scope}, appels: [{id, cle, entrees}], noeuds: set, transitions: [(source, cible)],
    erreur: ''} d'une définition de processus. XML mal formé : {"erreur": message}.
    """
    try:
        root = ET.fromstring(xml_text or "")
    except ET.ParseError as e:
        return {"erreur": f"XML mal formé : {e}"}
    variables = {v.get("name"): v.get("scope", "") for v in root if _local(v.tag) == "variable" and v.get("name")}
    calls, nodes, transitions = [], set(), []
    for element in root.iter():
        if element.get("id") and element is not root:
            nodes.add(element.get("id"))
        if _local(element.tag) in ("action-ref", "process-ref") and element.get("key"):
            inputs = [i.get("to") for m in element if _local(m.tag) == "mappings"
                      for i in m if _local(i.tag) == "input" and i.get("to")]
            calls.append({"id": element.get("id", ""), "cle": element.get("key"), "entrees": inputs})
    for element in root.iter():
        if _local(element.tag) in ("transitions", "initial-transitions"):
            owner = next((p.get("id", "") for p in root.iter() if element in list(p)), "")
            if element.get("default") is not None:
                transitions.append((owner, element.get("default")))
            for transition in element:
                if transition.get("to") is not None:
                    transitions.append((owner, transition.get("to")))
    return {"variables": variables, "appels": calls, "noeuds": nodes, "transitions": transitions, "erreur": ""}


def declared_inputs(definition: dict) -> set:
    return {name for name, scope in (definition.get("variables") or {}).items() if scope in ("in", "inout")}


def java_inputs(java_text: str) -> set:
    """Champs @In / @InOut d'une classe d'action."""
    text = re.sub(r"/\*[\s\S]*?\*/|//[^\n]*", " ", java_text or "")
    return {match.group(2) for match in _IN_FIELD.finditer(text)}


def split_key(key: str) -> tuple:
    """('wms-preparation', 'action', 'assign-need-preparationorder') d'une clé de composant ; () hors convention."""
    match = _KIND.match((key or "").split("/")[-1])
    return (match.group("prefix"), match.group("kind"), match.group("rest")) if match else ()


def find_definition(paths: list, key: str) -> list:
    """
    Fichiers candidats d'un composant dans une liste de chemins : processus <reste>-process.xml (ou seul
    *-process.xml) du dossier …/process/<reste en dossiers>/ ; action *.java de …/action/<reste en dossiers>/.
    """
    parts = split_key(key)
    if not parts:
        return []
    prefix, kind, rest = parts
    folder = f"/{prefix.replace('-', '/')}/{kind}/{rest.replace('-', '/')}/"
    direct = [p for p in paths if folder in p and "/" not in p.split(folder, 1)[1] and "/src/test/" not in p]
    if kind == "process":
        exact = [p for p in direct if p.endswith(f"/{rest}-process.xml")]
        return exact or [p for p in direct if p.endswith("-process.xml")]
    return [p for p in direct if p.endswith(".java")]


def read_inputs(kind: str, text: str):
    """Entrées déclarées d'un fichier de définition ; None s'il n'en est pas une."""
    if kind == "process":
        definition = parse_process(text)
        return None if definition.get("erreur") else declared_inputs(definition)
    return java_inputs(text) if "@Action" in (text or "") else None


# ---------------------------------------------------------------------------
# Version testée : bundle.list, forks, Standard
# ---------------------------------------------------------------------------

def bundle_versions(bundle_texts: list, client_group: str = "") -> dict:
    """
    {"standard": version importée (26.22), "forks": {composant: version}, "groupe": groupe du client} lus dans les
    bundle.list. Groupe du client : celui des lignes « runtime » hors groupe des imports (Standard), à défaut
    `client_group`.
    """
    lines = []
    for text in bundle_texts:
        for raw in (text or "").splitlines():
            match = _BUNDLE_LINE.match(raw)
            if match and not raw.lstrip().startswith("#"):
                lines.append(match.groups())
    imports = [(g, a, v) for kind, g, a, v in lines if kind == "import"]
    std_group = imports[0][0] if imports else ""
    standard = next((re.match(r"(\d+\.\d+)", v).group(1) for _g, a, v in imports
                     if re.match(r"\d+\.\d+", v)), "")
    group = client_group or next((g for kind, g, _a, _v in lines if kind == "runtime" and g != std_group), "")
    forks = {a: v for kind, g, a, v in lines if kind == "runtime" and g == group}
    complete = next((v for _g, _a, v in imports if re.match(r"\d+\.\d+", v)), "")
    return {"standard": standard, "standard_complet": complete, "groupe_standard": std_group, "forks": forks,
            "groupe": group}


class ClientSources:
    """Dépôts git de l'application du client à une référence (lecture par git, aucune écriture)."""

    def __init__(self, repos: dict, ref_for):
        self.repos = repos          # {nom: chemin}
        self.ref_for = ref_for      # chemin → référence git ('' : dépôt ignoré)
        self._files = {}

    def files(self, name: str) -> list:
        if name not in self._files:
            from tools.cucumber_tools import _git
            ref = self.ref_for(self.repos[name])
            self._files[name] = (ref, _git(self.repos[name], "ls-tree", "-r", "--name-only", ref).stdout.splitlines()
                                 if ref else [])
        return self._files[name][1]

    def show(self, name: str, path: str) -> str:
        from tools.cucumber_tools import _git
        self.files(name)
        return _git(self.repos[name], "show", f"{self._files[name][0]}:{path}").stdout

    def bundle_lists(self) -> list:
        texts = []
        for name in self.repos:
            if "cucumber" in name.lower():
                continue
            for path in self.files(name):
                if path.endswith("bundle.list") and ("/" + path).count("/src/main/"):
                    texts.append(self.show(name, path))
        return texts

    def definition(self, key: str) -> tuple:
        """(entrées, preuve) d'un composant dans les dépôts du client ; (None, '') s'il n'y est pas."""
        kind = (split_key(key) or ("", "", ""))[1]
        for name in self.repos:
            if "cucumber" in name.lower():
                continue
            for path in find_definition(self.files(name), key):
                inputs = read_inputs(kind, self.show(name, path))
                if inputs is not None:
                    return inputs, f"{name}/{path} @ {self._files[name][0]}"
        return None, ""


class Resolver:
    """Entrées déclarées d'un composant appelé, résolues comme l'environnement : Studio, fork du client, Standard."""

    def __init__(self, custom: dict, forks: dict = None, client: ClientSources = None, std=None,
                 std_candidates=(), std_version: str = ""):
        self.custom = custom                    # {composant: définition parsée}
        self.forks = forks or {}
        self.client = client
        self.std = std
        self.candidates = std_candidates
        self.std_version = std_version
        self.cache = {}

    def resolve(self, key: str) -> dict:
        """{entrees: set | None, source, preuve, etat: ok | absent | non_verifie}."""
        if key in self.cache:
            return self.cache[key]
        name = key.split("/")[-1]
        if name in self.custom and not self.custom[name].get("erreur"):
            out = {"entrees": declared_inputs(self.custom[name]), "source": "Studio",
                   "preuve": f"{name} (processus personnalisé déployé)", "etat": "ok"}
        elif not split_key(key):
            out = {"entrees": None, "source": "", "preuve": "clé hors convention de nommage", "etat": "non_verifie"}
        elif name in self.forks:
            inputs, proof = self.client.definition(key) if self.client else (None, "")
            out = ({"entrees": inputs, "source": f"fork du client {self.forks[name]}", "preuve": proof, "etat": "ok"}
                   if inputs is not None else
                   {"entrees": None, "source": f"fork du client {self.forks[name]}", "etat": "non_verifie",
                    "preuve": "fork listé dans le bundle.list, sources introuvables dans les dépôts du client"})
        else:
            out = self._standard_or_client(key)
        self.cache[key] = out
        return out

    def _standard_or_client(self, key: str) -> dict:
        prefix, kind, _rest = split_key(key)
        if self.std is None or not self.candidates:
            return {"entrees": None, "source": "", "etat": "non_verifie",
                    "preuve": "Standard non lu" if self.std is None else "version du Standard inconnue (bundle.list)"}
        try:
            home = f"{prefix}-{'workflow' if kind == 'process' else 'backend'}"
            repos = [r for r in self.std.repos() if r.startswith(prefix + "-")]
            if home not in repos:
                return {"entrees": None, "source": "", "etat": "non_verifie",
                        "preuve": f"dépôt {home} absent du projet du Standard (composant d'un autre projet)"}
            unresolved = []
            for repo in repos:
                ref = self.std.ref(repo, self.candidates)
                if not ref:
                    unresolved.append(repo)
                for path in find_definition(self.std.files(repo, ref) if ref else [], key):
                    inputs = read_inputs(kind, self.std.show(repo, ref, path))
                    if inputs is not None:
                        return {"entrees": inputs, "source": f"Standard {self.std_version}",
                                "preuve": f"{repo}/{path} @ {self.std.ref_name(repo, ref)}", "etat": "ok"}
        except Exception as e:  # noqa: BLE001 - Bitbucket injoignable : ni preuve, ni absence
            return {"entrees": None, "source": "", "preuve": f"Bitbucket injoignable ({type(e).__name__})",
                    "etat": "non_verifie"}
        if not repos:
            return {"entrees": None, "source": "", "preuve": f"aucun dépôt « {prefix}-* » dans le Standard",
                    "etat": "non_verifie"}
        if unresolved:
            return {"entrees": None, "source": "", "etat": "non_verifie",
                    "preuve": f"{', '.join(unresolved)} sans référence {self.std_version} dans le Standard"}
        return {"entrees": None, "source": f"Standard {self.std_version}", "etat": "absent",
                "preuve": f"absent des dépôts {', '.join(repos)} du Standard {self.std_version} (ni fork du "
                          f"client dans le bundle.list, ni processus Studio)"}


# ---------------------------------------------------------------------------
# Contrôle
# ---------------------------------------------------------------------------

def anomaly(text: str, component: str, file: str, line: int, impact: str, action: str, kind: str,
            proof: str = "", target: str = "") -> dict:
    return {"anomalie": text, "composant": component, "cible": target, "fichier": file, "ligne": line,
            "impact": impact, "action": action, "classe": kind, "preuve": proof}


def check_studio(components: list, resolver: Resolver) -> dict:
    """
    `components` : [{nom, version, statut, xml}] (dernière version de chaque processus personnalisé déployé).
    Renvoie {anomalies: [...], non_verifies: {cle: preuve}, controles: nombre d'appels contrôlés}.
    """
    anomalies, unverified, checked = [], {}, 0
    for component in components:
        name, xml = component["nom"], component.get("xml", "")
        where = f"Studio {name} v{component.get('version', '?')}"
        definition = resolver.custom.get(name) or parse_process(xml)
        if (component.get("statut") or VALID_STATUS) != VALID_STATUS:
            anomalies.append(anomaly(
                f"composant en {component['statut']}", name, where, 0,
                "processus non exécutable : tout scénario qui le traverse échoue",
                "redéployer le composant et ses dépendances dans Studio", ENVIRONMENT,
                "kis_custom_process_version.status"))
        if definition.get("erreur"):
            anomalies.append(anomaly(definition["erreur"], name, where, 0, "processus non chargeable",
                                     "corriger le XML dans Studio", PRODUCT))
            continue
        nodes = definition["noeuds"]
        for source, target in definition["transitions"]:
            if target not in nodes and target not in _END and not target.startswith("$"):
                anomalies.append(anomaly(
                    f"transition de « {source or 'début'} » vers « {target} », nœud inexistant", name, where,
                    _line_of(xml, f'"{target}"'), "le processus s'arrête en erreur à cette transition",
                    "corriger la transition dans Studio", PRODUCT))
        for call in definition["appels"]:
            checked += 1
            found = resolver.resolve(call["cle"])
            target = call["cle"].split("/")[-1]
            line = _line_of(xml, f'id="{call["id"]}"') if call["id"] else _line_of(xml, call["cle"])
            if found["etat"] == "absent":
                anomalies.append(anomaly(
                    f"composant référencé « {call['cle']} » absent de la version testée", name, where, line,
                    "le processus échoue à cet appel (composant introuvable)",
                    "déployer le composant ou retirer l'appel dans Studio", ENVIRONMENT, found["preuve"], target))
                continue
            if found["entrees"] is None:
                unverified.setdefault(call["cle"], found["preuve"])
                continue
            missing = [v for v in call["entrees"] if v not in found["entrees"]]
            if missing:
                anomalies.append(anomaly(
                    f"variable(s) {', '.join(missing)} passée(s) en entrée de « {target} » (nœud "
                    f"{call['id'] or '?'}) non déclarée(s) par {found['source']}", name, where, line,
                    "le moteur refuse l'appel à l'exécution (« Unable to find variable named "
                    f"{missing[0]} ») : tout scénario qui traverse ce nœud échoue",
                    f"aligner le processus personnalisé sur {found['source']} (variable renommée, retirée ou "
                    f"ajoutée dans une autre version) ou déployer la version qui la déclare", PRODUCT,
                    found["preuve"], target))
    return {"anomalies": anomalies, "non_verifies": unverified, "controles": checked}


def studio_components(schema: str, column: str = "coalesce(nullif(customized_xml, ''), nullif(merged_xml, ''), original_xml)") -> list:
    """Dernière version de chaque processus personnalisé de la base des tests (profil « cucumber » de db_tools)."""
    from tools import db_tools
    previous = db_tools.set_profile("cucumber")
    try:
        with db_tools.readonly_connection(schema) as conn:
            rows = conn.exec_driver_sql(
                "SELECT DISTINCT ON (component_name) component_name, version, status, "
                f"{column} FROM kis_custom_process_version ORDER BY component_name, version DESC").fetchall()
    except Exception:  # noqa: BLE001 - table absente ou base injoignable
        return []
    finally:
        db_tools.set_profile(previous)
    return [{"nom": r[0], "version": r[1], "statut": r[2] or "", "xml": r[3] or ""} for r in rows]


def analyse_studio(components: list, client_repos: dict, client_ref: str, std=None, std_candidates_for=None,
                   client_group: str = "") -> dict:
    """
    Analyse 1.a complète : version testée lue dans le bundle.list des dépôts du client à `client_ref`, Standard à la
    version importée, forks du client. Renvoie {version, anomalies, non_verifies, controles}.
    """
    from tools.cucumber_tools import pick_ref
    refs = (client_ref,) if client_ref else ()

    def ref_for(path):
        return pick_ref(path, refs) if refs else ""
    client = ClientSources(client_repos, ref_for)
    bundle = bundle_versions(client.bundle_lists(), client_group)
    candidates = std_candidates_for(bundle["standard"]) if (std_candidates_for and bundle["standard"]) else ()
    custom = {c["nom"]: parse_process(c.get("xml", "")) for c in components}
    resolver = Resolver(custom, bundle["forks"], client, std, candidates, bundle["standard"])
    result = check_studio(components, resolver)
    result["version"] = {"reference_client": client_ref, "standard": bundle["standard"],
                         "standard_complet": bundle["standard_complet"], "groupe_standard": bundle["groupe_standard"],
                         "forks": bundle["forks"], "groupe": bundle["groupe"]}
    result["_client"], result["_bundle"], result["_resolver"] = client, bundle, resolver
    return result


def _folder(key: str) -> str:
    parts = split_key(key)
    return f"/{parts[1]}/{parts[2].replace('-', '/')}/" if parts else ""


def features_at_risk(specs: list, anomalies: list) -> dict:
    """
    {id du spécifique: [raisons]} des fonctionnalités touchées par une anomalie : un de leurs fichiers est le
    processus Studio en cause (dossier <composant>) ou appartient au composant appelé (paquet …/<type>/<reste>/).
    """
    out = {}
    for spec in specs or []:
        paths = ["/" + str(f.get("path", "")).replace("\\", "/") for f in spec.get("fichiers") or []]
        for item in anomalies or []:
            caller, target = item.get("composant", ""), item.get("cible", "")
            folder = _folder(target)
            where = "/" + str(item.get("fichier", "")).replace("\\", "/")
            if any(f"/{caller}/" in p for p in paths if caller) or any(folder in p for p in paths if folder) or \
                    any(where.endswith(p) for p in paths if len(p) > 1):
                reason = f"{item['anomalie']} ({item['fichier']})"
                if reason not in out.setdefault(spec["id"], []):
                    out[spec["id"]].append(reason)
    return out


def fingerprint(components: list, client_commits: dict, std_version: str, configuration: dict = None) -> str:
    """
    Empreinte de la version testée : processus Studio déployés, commits du client à la référence, Standard, copie de
    référence (date et signature de version : une nouvelle copie relance l'analyse de la configuration).
    """
    import hashlib
    import json
    data = {"studio": sorted((c["nom"], str(c.get("version")), c.get("statut", "")) for c in components),
            "client": client_commits, "standard": std_version, "analyse": ANALYSIS_VERSION,
            "configuration": configuration or {}}
    return hashlib.sha256(json.dumps(data, sort_keys=True).encode("utf-8")).hexdigest()[:16]


def report_lines(result: dict) -> list:
    version = result.get("version") or {}
    lines = [f"Version testée : application @ {version.get('reference_client') or '?'}, Standard "
             f"{version.get('standard') or '?'}, {len(version.get('forks') or {})} fork(s) du client ; "
             f"{result.get('controles', 0)} appel(s) contrôlé(s), {len(result.get('anomalies') or [])} anomalie(s), "
             f"{len(result.get('non_verifies') or {})} composant(s) non vérifié(s)."]
    for a in result.get("anomalies") or []:
        lines.append(f"- [{a['classe']}] {a['anomalie']} — {a['fichier']}" + (f":{a['ligne']}" if a["ligne"] else "")
                     + f" — impact : {a['impact']} — action : {a['action']}"
                     + (f" — preuve : {a['preuve']}" if a.get("preuve") else ""))
    for key, why in sorted((result.get("non_verifies") or {}).items()):
        lines.append(f"- [non vérifié] {key} : {why}")
    code = result.get("code")
    if code is not None:
        lines.append(f"Code du client : {code.get('fichiers', 0)} fichier(s) déployé(s), "
                     f"{len(code.get('anomalies') or [])} anomalie(s), {len(code.get('non_verifies') or {})} "
                     f"élément(s) non vérifié(s).")
        for a in code.get("anomalies") or []:
            lines.append(f"- [{a['classe']}] {a['anomalie']} — {a['fichier']}"
                         + (f":{a['ligne']}" if a["ligne"] else "") + f" — impact : {a['impact']}")
        for key, why in sorted((code.get("non_verifies") or {}).items())[:15]:
            lines.append(f"- [non vérifié] {key} : {why}")
    config = result.get("config")
    if config is not None:
        lines.append(f"Configuration (copie de référence) : {config.get('controles', 0)} colonne(s) lue(s), "
                     f"{len(config.get('branchements') or [])} branchement(s) externe(s), "
                     f"{len(config.get('anomalies') or [])} anomalie(s), {len(config.get('non_verifies') or {})} "
                     f"élément(s) non vérifié(s).")
        for a in config.get("anomalies") or []:
            lines.append(f"- [{a['classe']}] {a['anomalie']} — {a['fichier']} — action : {a['action']}")
        for key, why in sorted((config.get("non_verifies") or {}).items())[:15]:
            lines.append(f"- [non vérifié] {key} : {why}")
    return lines


def all_anomalies(result: dict) -> list:
    """Anomalies des trois analyses (Studio, code du client, configuration)."""
    return (list(result.get("anomalies") or []) + list((result.get("code") or {}).get("anomalies") or [])
            + list((result.get("config") or {}).get("anomalies") or []))


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Analyse statique d'anticipation (Studio, point 1.a)")
    parser.add_argument("--client", required=True)
    parser.add_argument("--client-root", required=True, help="dossier des dépôts git de l'application du client")
    parser.add_argument("--ref", default="", help="référence de la version testée (ex. origin/v1.3, "
                                                  "origin/integration) ; défaut : celle de la version choisie")
    parser.add_argument("--schema", default="", help="schéma de la base des tests (défaut : celui du profil)")
    args = parser.parse_args(argv)
    try:
        from dotenv import load_dotenv
        load_dotenv(os.path.join(ROOT, ".env"))
    except ImportError:  # pragma: no cover
        pass
    from tools import client_version, cucumber_copied_config, cucumber_profile, cucumber_tools
    from tools.bitbucket_code import standard
    from tools.cucumber_jobs import _std_candidates
    profile = cucumber_profile.effective_profile(cucumber_profile.load_profile(args.client))
    schema = args.schema or cucumber_copied_config.test_schema(profile) or os.getenv("CUCUMBER_DB_SCHEMA", "")
    ref = args.ref or ("origin/" + (client_version.saved(args.client).get("deduction") or {}).get("ref_client", ""))
    try:
        std = standard("")
    except Exception as e:  # noqa: BLE001
        print(f"Standard illisible : {e}")
        std = None
    from tools import cucumber_anticipation_code
    result = cucumber_anticipation_code.analyse_all(studio_components(schema), cucumber_tools.git_repos(args.client_root),
                                                    ref, schema, std, _std_candidates, cucumber_copied_config.copy_rows)
    print("\n".join(report_lines(result)))
    return 1 if all_anomalies(result) else 0


if __name__ == "__main__":
    sys.exit(main())
