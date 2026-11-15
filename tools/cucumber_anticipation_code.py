"""
Analyse statique d'anticipation, point 1.b : code du client à la version testée (0 token, sans run), tous clients.

Code contrôlé : modules DÉPLOYÉS du client (lignes « runtime:<groupe du client>/<module>@… » du bundle.list de son
application, à la référence de la version testée), sources src/main ; plus ses processus Studio (base des tests).

1. Appels au Standard : classe importée absente, méthode absente ou de nombre d'arguments différent, dans les jars
   du Standard À LA VERSION IMPORTÉE par le bundle.list (dépôt Maven : release, ou snapshot pour une version
   -SNAPSHOT), hiérarchie comprise (classe mère et interfaces) ; une classe forkée par le client n'est pas comparée au
   Standard ; jar ou hiérarchie illisible : non vérifié, jamais une anomalie.
2. Énumérations : valeur d'énumération (Java « xxxEnumYyy.VALEUR() », Studio « $XxxEnumYyy.VALEUR() ») absente de la
   table d'énumération de la base des tests (<module>_enum_<nom> : colonne id).
3. Clés de traduction : clé « $translate.message("…") » ajoutée par un processus Studio du client (absente de
   l'original du Standard) sans traduction dans son fichier i18n.fr_FR.txt (dépôt Studio du client).
4. Champs personnalisés : customString1… lu ou écrit (Java get/set, Studio « .customString1 ») sur une entité qui ne
   le déclare pas (kis_custom_field de la base des tests).
5. Traitement conditionné à un niveau de log : bloc « if (log.isDebugEnabled()) { … } » qui fait autre chose que
   journaliser — le comportement change avec le niveau de log de l'environnement.
6. URL et hôtes de services externes écrits en dur (code et ressources) : absents de l'environnement de test.

Anomalies au format de cucumber_anticipation (anomalie, composant, cible, fichier, ligne, impact, action, classe,
preuve) : « défaut produit probable » ou « risque d'environnement ».
"""
import json
import os
import re
import zipfile

from tools.cucumber_anticipation import ENVIRONMENT, PRODUCT, anomaly, parse_process

STD_JAR_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), ".tmp", "std_jars")
_CUSTOM = r"custom(?:String|Long|Date|Boolean|Double|Decimal|Integer|Text)\d+"
_IMPORT = re.compile(r"^\s*import\s+(?!static\b)([\w.]+?)\s*;", re.M)
_PACKAGE = re.compile(r"^\s*package\s+([\w.]+)\s*;", re.M)
_TYPED = re.compile(r"\b([A-Z]\w*(?:\.[A-Z]\w*)?)(?:\s*<[^;(){}]*?>)?\s+([a-z_]\w*)\s*(?=[=;,)])")
_CALL = re.compile(r"(?<![\w.])([A-Za-z_]\w*)\s*\.\s*([A-Za-z_]\w*)\s*(?=\()")
_LOG_GUARD = re.compile(r"\bif\s*\(\s*(?:this\.)?([A-Za-z_]\w*)\s*\.\s*is(Trace|Debug|Info)Enabled\s*\(\s*\)\s*\)")
_URL = re.compile(r"\b(?:https?|ftp|sftp|tcp|amqp|ldap|jdbc:\w+)://([A-Za-z0-9.-]+)(?::(\d+))?[^\s\"'<>]*", re.I)
_IP = re.compile(r"(?<![\d.])((?:\d{1,3}\.){3}\d{1,3})(?::(\d{2,5}))?(?![\d.])")
_NAMESPACE_HOSTS = re.compile(r"(^|\.)(w3\.org|xmlsoap\.org|java\.sun\.com|jcp\.org|apache\.org|springframework\.org|"
                              r"oasis-open\.org|example\.(com|org)|schemas\.\w+|maven\.org|eclipse\.org|"
                              r"gs1\.org|unece\.org|opengis\.net|hibernate\.org|jboss\.org|localhost)$", re.I)
_LOCAL_IPS = re.compile(r"^(127\.|0\.0\.0\.0$|255\.|1\.0\.0\.\d+$)")
_STUDIO_ENUM = re.compile(r"\$((?:[A-Z][a-z]+)+Enum\w*)\.([A-Z][A-Z0-9_]+)\(\)")
_JAVA_ENUM_VALUE = re.compile(r"(?<![\w.])([A-Za-z_]\w*)\s*\.\s*([A-Z][A-Z0-9_]{1,})\s*\(\s*\)")
_TRANSLATE = re.compile(r"""\$translate\.message\(\s*["'](?:&quot;)?(.*?)(?:&quot;)?["']\s*[,)]""")
GENERIC_MAPPING_FIELDS = 5
_RESOURCE_EXT = (".properties", ".yml", ".yaml", ".json", ".xml", ".conf", ".txt")


def _line(text: str, index: int) -> int:
    return text.count("\n", 0, index) + 1


def _blank(text: str) -> str:
    from tools.cucumber_library import _blank_comments_and_strings
    return _blank_comments_and_strings(text)


def typed_variables(code: str) -> dict:
    """
    {variable: type déclaré (« Type » ou « Externe.Interne »)} des déclarations d'un fichier ; une variable déclarée
    avec plusieurs types différents (portées différentes) est ambiguë et ignorée.
    """
    seen = {}
    for type_name, var in _TYPED.findall(code):
        seen.setdefault(var, set()).add(type_name)
    for type_name, var in re.findall(r"\b(?:[a-z]\w*\.)+([A-Z]\w*)(?:\s*<[^;(){}]*?>)?\s+([a-z_]\w*)\s*(?=[=;,)])",
                                     code):
        seen.setdefault(var, set()).add("<qualifié>" + type_name)
    return {var: next(iter(types)) for var, types in seen.items() if len(types) == 1}


_COMMENT_OR_STRING = re.compile(r'"(?:\\.|[^"\\\n])*"' + r"|'(?:\\.|[^'\\\n])*'" + r"|/\*[\s\S]*?\*/|//[^\n]*")


def _strip_comments(text: str) -> str:
    """Commentaires remplacés par des espaces (lignes conservées), chaînes intactes (« http:// » n'est pas un
    commentaire)."""
    def keep(match):
        token = match.group(0)
        return token if token[0] in "\"'" else re.sub(r"[^\n]", " ", token)
    return _COMMENT_OR_STRING.sub(keep, text or "")


# ---------------------------------------------------------------------------
# Code déployé du client
# ---------------------------------------------------------------------------

def pom_artifact(pom: str) -> str:
    """artifactId propre d'un pom (hors parent et dépendances)."""
    text = re.sub(r"<parent>[\s\S]*?</parent>|<dependencies>[\s\S]*?</dependencies>|"
                  r"<dependencyManagement>[\s\S]*?</dependencyManagement>|<build>[\s\S]*?</build>|"
                  r"<profiles>[\s\S]*?</profiles>|<!--[\s\S]*?-->", "", pom or "")
    match = re.search(r"<artifactId>\s*([\w.-]+)\s*</artifactId>", text)
    return match.group(1) if match else ""


def deployed_sources(client, modules) -> list:
    """
    [(dépôt, chemin)] des sources src/main des modules déployés du client (`modules` : artefacts du bundle.list).
    `client` : cucumber_anticipation.ClientSources.
    """
    wanted, out = set(modules or ()), []
    for name in client.repos:
        if "cucumber" in name.lower():
            continue
        files = client.files(name)
        roots = []
        for pom in (f for f in files if f.endswith("pom.xml")):
            if pom_artifact(client.show(name, pom)) in wanted:
                roots.append(pom[:-len("pom.xml")])
        for root in roots:
            out += [(name, f) for f in files if f.startswith(root + "src/main/")
                    and not any(f.startswith(other + "src/main/") for other in roots if len(other) > len(root))]
    return out


def client_classes(sources: list) -> set:
    """Classes déclarées par le code du client (paquet + nom du fichier) : jamais comparées au Standard."""
    out = set()
    for _repo, path in sources:
        if path.endswith(".java") and "/java/" in path:
            out.add(path.split("/java/", 1)[1][:-5].replace("/", "."))
    return out


# ---------------------------------------------------------------------------
# 1. Appels au Standard (jars à la version)
# ---------------------------------------------------------------------------

class StandardApi:
    """Classes du Standard lues dans ses jars à la version (dépôt Maven), chargées à la demande par paquet."""

    def __init__(self, group: str, version: str, fetch=None, cache_dir: str = STD_JAR_DIR):
        self.group, self.version = group, version
        self.fetch = fetch or self._http
        self.cache_dir = cache_dir
        self.classes, self.artifacts, self.errors = {}, {}, {}

    # Accès Maven -------------------------------------------------------------------------------------------------
    @staticmethod
    def repositories() -> tuple:
        from tools import cucumber_job
        release, auth = cucumber_job.maven_repository()
        snapshot = os.getenv("MAVEN_SNAPSHOT_REPOSITORY_URL", "").strip().rstrip("/") or (
            release[:-len("release")] + "snapshot" if release.endswith("/release") else release)
        return release, snapshot, auth

    def _http(self, path: str) -> bytes:
        import requests
        release, snapshot, auth = self.repositories()
        base = snapshot if self.version.endswith("-SNAPSHOT") else release
        if not base:
            raise RuntimeError("MAVEN_REPOSITORY_URL non configuré")
        response = requests.get(f"{base}/{path}", auth=auth, timeout=60)
        if response.status_code == 404:
            return b""
        response.raise_for_status()
        return response.content

    def _jar(self, artifact: str) -> str:
        """Chemin local du jar de l'artefact à la version ('' s'il n'existe pas) ; lève sur un dépôt illisible."""
        folder = f"{self.group.replace('.', '/')}/{artifact}/{self.version}"
        name = f"{artifact}-{self.version}.jar"
        if self.version.endswith("-SNAPSHOT"):
            meta = self.fetch(f"{folder}/maven-metadata.xml").decode("utf-8", errors="replace")
            if not meta:
                return ""
            values = re.findall(r"<snapshotVersion>\s*<extension>jar</extension>\s*<value>([^<]+)</value>", meta) \
                or re.findall(r"<extension>jar</extension>\s*<value>([^<]+)</value>", meta)
            if not values:
                return ""
            name = f"{artifact}-{values[0]}.jar"
        local = os.path.join(self.cache_dir, self.group, artifact, name)
        if not os.path.exists(local):
            data = self.fetch(f"{folder}/{name}")
            if not data:
                return ""
            os.makedirs(os.path.dirname(local), exist_ok=True)
            with open(local, "wb") as f:
                f.write(data)
        return local

    def _load(self, artifact: str) -> bool:
        if artifact in self.artifacts:
            return bool(self.artifacts[artifact])
        try:
            jar = self._jar(artifact)
        except Exception as e:  # noqa: BLE001 - dépôt Maven illisible : non vérifié
            self.errors[artifact] = f"dépôt Maven illisible ({type(e).__name__})"
            self.artifacts[artifact] = ""
            return False
        self.artifacts[artifact] = jar
        if jar:
            index = jar + ".api.json"
            try:
                with open(index, encoding="utf-8") as f:
                    classes = json.load(f)
            except (OSError, ValueError):
                from tools.cucumber_library import read_jar
                try:
                    classes = read_jar(jar)
                except (OSError, zipfile.BadZipFile):
                    classes = {}
                with open(index, "w", encoding="utf-8") as f:
                    json.dump(classes, f)
            self.classes.update(classes)
        return bool(jar)

    def artifact_of(self, package: str) -> str:
        """Artefact du paquet (com.asis.wms.preparation.datamodel → wms-preparation-datamodel), '' s'il n'existe pas."""
        parts = package.split(".")
        root = 2 if len(parts) > 2 and parts[0] in ("com", "org", "fr", "net", "io") else 1
        for end in range(len(parts), root + 1, -1):
            artifact = "-".join(parts[root:end])
            if self._load(artifact):
                return artifact
        return ""

    def has_class(self, fqn: str):
        """True / False (jar du paquet lu) ; None si aucun jar ne correspond au paquet (non vérifié)."""
        if fqn in self.classes:
            return True
        parts = fqn.split(".")
        first_type = next((i for i, part in enumerate(parts) if part[:1].isupper()), len(parts) - 1)
        package, nested = ".".join(parts[:first_type]), ".".join(parts[:first_type + 1]) + "".join(
            "$" + p for p in parts[first_type + 1:])
        artifact = self.artifact_of(package)
        if not artifact:
            return None
        return fqn in self.classes or nested in self.classes

    def lookup(self, fqn: str, method: str) -> tuple:
        """(arités connues, hiérarchie entièrement lue) de `method` sur `fqn`, classe mère et interfaces."""
        arities, complete, pending, seen = [], True, [fqn], set()
        while pending:
            current = pending.pop()
            if not current or current in seen:
                continue
            seen.add(current)
            if current == "java.lang.Object":
                from tools.cucumber_library import _OBJECT_METHODS
                arities += _OBJECT_METHODS.get(method, [])
                continue
            info = self.classes.get(current)
            if info is None and current.startswith("com.") and self.has_class(current):
                info = self.classes.get(current)
            if info is None:
                complete = False
                continue
            for entry in info["methodes"].get(method, []):
                arities.append(-entry[0] if entry[2] else entry[0])
            pending += [info.get("super", "")] + list(info.get("interfaces") or [])
        return arities, complete


def check_api(text: str, rel: str, api: StandardApi, own: set, prefix: str) -> tuple:
    """
    (anomalies, non vérifiés) des appels du fichier Java `text` vers le Standard (imports « `prefix`.* » hors classes
    du client `own`).
    """
    from tools.cucumber_library import _accepts, _arguments
    code = _blank(text)
    anomalies, unverified, imported = [], set(), {}
    for fqn in _IMPORT.findall(code):
        if not fqn.startswith(prefix) or fqn in own or any(fqn.startswith(c + ".") for c in own):
            continue
        found = api.has_class(fqn)
        if found is None:
            unverified.add(fqn.rsplit(".", 1)[0])
            continue
        if not found:
            anomalies.append(anomaly(
                f"classe {fqn} absente du Standard {api.version}", rel, rel,
                _line(code, code.find(fqn)), "le module du client ne démarre pas (classe introuvable) : tous les "
                                               "scénarios qui le traversent échouent",
                f"aligner le code sur le Standard {api.version}", PRODUCT, f"jar {api.artifact_of(fqn.rsplit('.', 1)[0])}",
                fqn))
            continue
        imported[fqn.rsplit(".", 1)[1]] = fqn
    if not imported:
        return anomalies, unverified
    typed = {}
    for var, type_name in typed_variables(code).items():
        outer, _, inner = type_name.partition(".")
        if outer in imported:
            typed[var] = imported[outer] + (f"${inner}" if inner else "")
    for match in _CALL.finditer(code):
        receiver, method = match.group(1), match.group(2)
        fqn = imported.get(receiver) or typed.get(receiver)
        if not fqn or method in ("class",) or re.search(r"\bnew\s+(?:[\w.]+\.)?$", code[:match.start()]):
            continue
        arities, complete = api.lookup(fqn, method)
        if not complete:
            continue
        simple = fqn.rsplit(".", 1)[1].replace("$", ".")
        line = _line(code, match.start())
        if not arities:
            anomalies.append(anomaly(
                f"{simple}.{method}() n'existe pas dans le Standard {api.version}", rel, rel, line,
                "erreur à l'exécution (NoSuchMethodError) : tout scénario qui passe par cet appel échoue",
                f"aligner l'appel sur l'API du Standard {api.version}", PRODUCT, f"{fqn} (jar du Standard)", fqn))
            continue
        count, _ = _arguments(code, code.index("(", match.end()))
        if count is not None and not _accepts(arities, count):
            anomalies.append(anomaly(
                f"{simple}.{method} : {count} argument(s), le Standard {api.version} en attend "
                + " ou ".join(str(a) if a >= 0 else f"au moins {-a - 1}" for a in sorted(set(arities))),
                rel, rel, line, "erreur à l'exécution (NoSuchMethodError)",
                f"aligner l'appel sur l'API du Standard {api.version}", PRODUCT, f"{fqn} (jar du Standard)", fqn))
    return anomalies, unverified


# ---------------------------------------------------------------------------
# 2-4. Énumérations, traductions, champs personnalisés
# ---------------------------------------------------------------------------

def enum_table(enum_class: str) -> str:
    """WmsEnumCubingType → wms_enum_cubing_type."""
    return re.sub(r"(?<!^)(?=[A-Z])", "_", enum_class).lower()


class Declarations:
    """Valeurs d'énumération et champs personnalisés déclarés dans la base des tests (lus à la demande)."""

    def __init__(self, schema: str, query):
        self.schema, self.query = schema, query
        self._tables, self._values, self._fields = None, {}, None

    def _rows(self, sql: str) -> list:
        try:
            return [str(r.get("code", "")) for r in self.query(sql, self.schema)]
        except Exception:  # noqa: BLE001 - base illisible : non vérifié
            return None

    def enum_values(self, enum_class: str):
        """Valeurs déclarées (set) ; None si la table n'existe pas ou la base est illisible."""
        table = enum_table(enum_class)
        if self._tables is None:
            rows = self._rows("SELECT table_name AS code FROM information_schema.tables WHERE table_schema = "
                              "current_schema() AND table_name LIKE '%\\_enum\\_%'")
            self._tables = set(rows or [])
        if table not in self._tables:
            return None
        if table not in self._values:
            rows = self._rows(f'SELECT id::text AS code FROM "{table}"')
            self._values[table] = set(rows) if rows is not None else None
        return self._values[table]

    def custom_fields(self):
        """{entité (classe complète): champs déclarés} ; None si illisible."""
        if self._fields is None:
            rows = self._rows("SELECT entity || '@@' || coalesce(fields::text, '[]') AS code FROM kis_custom_field")
            if rows is None:
                return None
            self._fields = {}
            for row in rows:
                entity, _, fields = row.partition("@@")
                try:
                    names = {f.get("name") for f in json.loads(fields) if isinstance(f, dict)}
                except ValueError:
                    names = set(re.findall(rf"\b({_CUSTOM})\b", fields))
                self._fields.setdefault(entity, set()).update(names)
        return self._fields


def check_enums_java(text: str, rel: str, declarations: Declarations) -> list:
    code = _blank(text)
    typed = {}
    for var, type_name in typed_variables(code).items():
        outer = type_name.split(".")[0]
        if "Enum" in outer and not outer.startswith("<"):
            typed[var] = outer
    out = []
    for match in _JAVA_ENUM_VALUE.finditer(code):
        receiver, value = match.group(1), match.group(2)
        enum_class = typed.get(receiver) or (receiver if "Enum" in receiver and receiver[0].isupper() else "")
        if not enum_class:
            continue
        values = declarations.enum_values(enum_class)
        if values is not None and value not in values:
            out.append(anomaly(
                f"valeur {value} de l'énumération {enum_class} non déclarée", rel, rel, _line(code, match.start()),
                "erreur à l'exécution (valeur inconnue) dans tout scénario qui passe par ce code",
                f"déclarer {value} dans {enum_table(enum_class)} (init du client) ou corriger le code", PRODUCT,
                f"table {enum_table(enum_class)} de la base des tests", enum_class))
    return out


def _studio_types(xml: str) -> dict:
    return dict(re.findall(r'<variable\s+name="(\w+)"\s+type="([\w.$]+)"', xml or ""))


def check_studio_texts(components: list, originals: dict, i18n, declarations: Declarations) -> list:
    """
    Studio : énumérations, clés de traduction ajoutées par le client, champs personnalisés. `originals` :
    {composant: XML original du Standard} ; `i18n(composant)` → texte de i18n.fr_FR.txt ou None (introuvable).
    """
    out = []
    fields = declarations.custom_fields()
    for component in components:
        name, xml = component["nom"], component.get("xml", "")
        where = f"Studio {name} v{component.get('version', '?')}"
        if parse_process(xml).get("erreur"):
            continue
        for match in _STUDIO_ENUM.finditer(xml):
            enum_class, value = match.group(1), match.group(2)
            values = declarations.enum_values(enum_class)
            if values is not None and value not in values:
                out.append(anomaly(
                    f"valeur {value} de l'énumération {enum_class} non déclarée", name, where,
                    _line(xml, match.start()), "le processus échoue à l'évaluation de cette expression",
                    f"déclarer {value} dans {enum_table(enum_class)} (init du client) ou corriger le processus",
                    PRODUCT, f"table {enum_table(enum_class)} de la base des tests", enum_class))
        added = set(_TRANSLATE.findall(xml)) - set(_TRANSLATE.findall(originals.get(name, "")))
        if added:
            text = i18n(name)
            known = {line.split(":", 1)[0].strip() for line in (text or "").splitlines() if ":" in line}
            for key in sorted(added):
                if text is not None and key not in known:
                    out.append(anomaly(
                        f"clé de traduction « {key} » sans traduction française", name, where,
                        _line(xml, xml.find(key)), "libellé affiché tel quel (non traduit) : un test qui attend le libellé "
                                         "français échoue", f"ajouter « {key}:… » à {name}/i18n.fr_FR.txt",
                        PRODUCT, f"{name}/i18n.fr_FR.txt du dépôt Studio du client"))
        if fields is None:
            continue
        types = _studio_types(xml)
        for match in re.finditer(rf"\b(\w+)\s*\.\s*(?:get|set)?({_CUSTOM}|Custom\w+?\d+)\b", xml):
            var, field = match.group(1), match.group(2)
            field = field[0].lower() + field[1:]
            entity = types.get(var, "")
            if entity and field not in fields.get(entity, set()):
                out.append(anomaly(
                    f"champ personnalisé {field} de {entity.rsplit('.', 1)[-1]} non déclaré", name, where,
                    _line(xml, match.start()), "champ sans alias (kis_custom_field) : invisible à l'écran et "
                                                "dans les fichiers d'intégration ; un test qui le lit par son "
                                                "alias échoue", f"déclarer {field} sur {entity} (init du client)",
                    PRODUCT, "kis_custom_field de la base des tests", entity))
    return out


def check_custom_fields_java(text: str, rel: str, declarations: Declarations) -> list:
    fields = declarations.custom_fields()
    if fields is None:
        return []
    code = _blank(text)
    imported = {fqn.rsplit(".", 1)[1]: fqn for fqn in _IMPORT.findall(code)}
    package = (_PACKAGE.findall(code) or [""])[0]
    typed = {var: imported.get(t, f"{package}.{t}") for var, t in typed_variables(code).items()
             if "." not in t and not t.startswith("<")}
    uses = []
    for match in re.finditer(rf"(?<![\w.])([a-z_]\w*)\s*\.\s*(?:get|set)(Custom(?:String|Long|Date|Boolean|Double|"
                             rf"Decimal|Integer|Text)\d+)\s*\(", code):
        var, field = match.group(1), match.group(2)
        field = field[0].lower() + field[1:]
        entity = typed.get(var, "")
        if entity in fields:
            uses.append((entity, field, match.start()))
    # Recopie générique de tous les champs personnalisés d'une entité (mappage) : pas un usage métier
    per_entity = {}
    for entity, field, _start in uses:
        per_entity.setdefault(entity, set()).add(field)
    out = []
    for entity, field, start in uses:
        if field in fields[entity] or len(per_entity[entity]) >= GENERIC_MAPPING_FIELDS:
            continue
        out.append(anomaly(
            f"champ personnalisé {field} de {entity.rsplit('.', 1)[-1]} non déclaré", rel, rel, _line(code, start),
            "champ sans alias (kis_custom_field) : invisible à l'écran et dans les fichiers d'intégration ; un test "
            "qui le lit par son alias échoue", f"déclarer {field} sur {entity} (init du client)", PRODUCT,
            "kis_custom_field de la base des tests", entity))
    return out


# ---------------------------------------------------------------------------
# 5-6. Niveau de log, adresses en dur
# ---------------------------------------------------------------------------

def _block(code: str, start: int) -> tuple:
    """(texte, fin) du bloc ou de l'instruction qui suit `start`."""
    i = start
    while i < len(code) and code[i].isspace():
        i += 1
    if i < len(code) and code[i] == "{":
        depth = 0
        for j in range(i, len(code)):
            depth += code[j] == "{"
            depth -= code[j] == "}"
            if depth == 0:
                return code[i + 1:j], j
        return code[i + 1:], len(code)
    end = code.find(";", i)
    return code[i:end + 1 if end >= 0 else len(code)], end


_CONTROL = re.compile(r"(?:for|if|else|while|switch|case|default|try|catch|finally|do|break|continue)\b")
_LOCAL_DECLARATION = re.compile(r"(?:final\s+)?[A-Za-z_][\w.]*(?:\s*<[^;()]*>)?(?:\[\])*\s+[a-z_]\w*\s*(?:=|$)")


def check_log_level(text: str, rel: str) -> list:
    code = _blank(text)
    out = []
    for match in _LOG_GUARD.finditer(code):
        logger = match.group(1)
        body, _ = _block(code, match.end())
        fragments = [f.strip() for f in re.split(r";|\{|\}", body)]
        fragments = [f for f in fragments if f]
        locals_ = set(re.findall(r"(?:^|[;{}(])\s*(?:final\s+)?[A-Za-z_][\w.]*(?:\s*<[^;()]*>)?(?:\[\])*\s+"
                                 r"([a-z_]\w*)\s*[=;]", body))
        effects = []
        for index, fragment in enumerate(fragments):
            following = fragments[index + 1] if index + 1 < len(fragments) else ""
            if fragment.startswith(f"{logger}.") or _CONTROL.match(fragment) or _LOCAL_DECLARATION.match(fragment):
                continue
            # Lambda dont le corps ne fait que journaliser : x.forEach(y -> logger.debug(…)), ifPresent, thenAccept
            nexts = [f for f in fragments[index + 1:] if not _CONTROL.match(f)]
            if re.search(rf"->\s*{re.escape(logger)}\.", fragment) or (
                    fragment.rstrip().endswith("->") and nexts and nexts[0].startswith(f"{logger}.")):
                continue
            if any(re.match(rf"{re.escape(v)}\s*(?:\.\s*append\b|\+?=)", fragment) for v in locals_):
                continue
            if re.fullmatch(r"\)+", fragment):
                continue
            effects.append(fragment)
        if effects:
            out.append(anomaly(
                f"traitement conditionné au niveau de log ({logger}.is{match.group(2)}Enabled) : "
                f"« {' '.join(effects[0].split())[:120]} »", rel, rel, _line(code, match.start()),
                "le comportement dépend du niveau de log de l'environnement : un test peut passer ou échouer selon "
                "l'environnement", "sortir le traitement du bloc de journalisation", PRODUCT))
    return out


def external_hosts(text: str) -> list:
    """
    [(hôte, port, position)] des adresses de services externes d'un texte : URL (hôte hors espaces de noms XML et
    hors local) et adresses IP citées seules dans une chaîne, avec un port ou en valeur d'un paramètre (« = »).
    """
    found = []
    for match in _URL.finditer(text or ""):
        host = match.group(1)
        if host and not _NAMESPACE_HOSTS.search(host) and not _LOCAL_IPS.match(host):
            found.append((host, match.group(2) or "", match.start()))
    in_url = [(m.start(), m.end()) for m in _URL.finditer(text or "")]
    for match in _IP.finditer(text or ""):
        ip, port = match.group(1), match.group(2)
        if any(start <= match.start() < end for start, end in in_url) or _LOCAL_IPS.match(ip):
            continue
        if not all(0 <= int(p) <= 255 for p in ip.split(".")):
            continue
        before = (text[:match.start()].rstrip()[-1:] if text[:match.start()].strip() else "")
        after = text[match.end():match.end() + 1]
        quoted = before in ('"', "'") and after in ('"', "'", "/")
        if port or quoted or (before in ("=", ":") and after in ("", "\n", "\r", " ", "/")):
            found.append((ip, port or "", match.start()))
    return found


def check_hardcoded_hosts(text: str, rel: str) -> list:
    if rel.endswith(("pom.xml", ".xsd", "bundle.list")):
        return []
    body = _strip_comments(text) if rel.endswith(".java") else re.sub(
        r"xmlns(?::\w+)?=\"[^\"]*\"|xsi:schemaLocation=\"[^\"]*\"|<!--[\s\S]*?-->",
        lambda m: re.sub(r"[^\n]", " ", m.group(0)), text or "")
    out = []
    for host, port, position in external_hosts(body):
        out.append(anomaly(
            f"adresse de service externe écrite en dur : {host}" + (f":{port}" if port else ""), rel, rel,
            _line(body, position), "service absent de l'environnement de test : l'appel échoue ou attend son délai",
            "lire l'adresse dans la configuration (paramètre, variable d'environnement) et la neutraliser en test",
            ENVIRONMENT, "adresse écrite dans le code", host))
    return out


# ---------------------------------------------------------------------------
# Analyse complète
# ---------------------------------------------------------------------------

def analyse_code(client, bundle: dict, components: list, originals: dict, declarations: Declarations,
                 api: StandardApi = None, studio_i18n=None, std_prefix: str = "com.asis.") -> dict:
    """
    Analyse 1.b. `client` : ClientSources (référence de la version testée) ; `bundle` : bundle_versions(…) ;
    `components` / `originals` : processus Studio de la base des tests. Renvoie {anomalies, non_verifies, fichiers}.
    """
    sources = deployed_sources(client, list(bundle.get("forks") or {}))
    own = client_classes(sources)
    anomalies, unverified = [], {}
    for repo, path in sources:
        rel = f"{repo}/{path}"
        if path.endswith(".java"):
            text = client.show(repo, path)
            if api is not None:
                found, missing = check_api(text, rel, api, own, std_prefix)
                anomalies += found
                for package in missing:
                    unverified.setdefault(package, "aucun jar du Standard pour ce paquet à la version")
            anomalies += check_enums_java(text, rel, declarations)
            anomalies += check_custom_fields_java(text, rel, declarations)
            anomalies += check_log_level(text, rel)
            anomalies += check_hardcoded_hosts(text, rel)
        elif "/src/main/resources/" in f"/{path}" and path.endswith(_RESOURCE_EXT):
            anomalies += check_hardcoded_hosts(client.show(repo, path), rel)
    anomalies += check_studio_texts(components, originals, studio_i18n or (lambda name: None), declarations)
    if api is not None:
        for artifact, error in api.errors.items():
            unverified.setdefault(artifact, error)
    return {"anomalies": anomalies, "non_verifies": unverified, "fichiers": len(sources)}


def studio_i18n_reader(repos: dict, ref: str):
    """Lecteur des traductions françaises d'un processus Studio (dépôt « studio » du client, à `ref` ou HEAD)."""
    from tools.cucumber_tools import _git, pick_ref
    studio = next((path for name, path in repos.items() if name.lower() == "studio"), "")
    cache = {}

    def read(component):
        if not studio:
            return None
        if "listing" not in cache:
            cache["ref"] = pick_ref(studio, (ref, "origin/HEAD"))
            cache["listing"] = _git(studio, "ls-tree", "-r", "--name-only", cache["ref"]).stdout.splitlines()                 if cache["ref"] else []
        path = next((f for f in cache["listing"] if f.endswith(f"{component}/i18n.fr_FR.txt")), "")
        return _git(studio, "show", f"{cache['ref']}:{path}").stdout if path else None
    return read


def analyse_all(components: list, repos: dict, ref: str, schema: str, std, candidates_for, query) -> dict:
    """Analyse d'anticipation 1.a (Studio) + 1.b (code du client) à la version testée."""
    from tools import cucumber_anticipation
    result = cucumber_anticipation.analyse_studio(components, repos, ref, std, candidates_for)
    sources, bundle = result.pop("_client"), result.pop("_bundle")
    api = (StandardApi(bundle["groupe_standard"], bundle["standard_complet"])
           if bundle.get("groupe_standard") and bundle.get("standard_complet") else None)
    originals = {c["nom"]: c["xml"] for c in cucumber_anticipation.studio_components(schema, "original_xml")}
    result["code"] = analyse_code(sources, bundle, components, originals, Declarations(schema, query), api,
                                  studio_i18n_reader(repos, ref))
    return result
