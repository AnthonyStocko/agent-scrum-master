"""
Analyse statique d'anticipation, point 1.c : configuration de la copie de référence (base des tests), 0 token, sans
run, tous clients, sans valeur en dur.

1. Branchements vers des services externes : colonnes d'adresse, d'hôte, d'URL, de file, d'imprimante… des tables
   de configuration dont une valeur ressemble à une adresse (hors localhost). Seul l'hôte est gardé, jamais un
   identifiant, un chemin ou un mot de passe.
2. Paramètres pointant vers des objets absents (références textuelles, que la base ne vérifie pas) :
   - colonnes d'énumération sans clé étrangère (colonne nommée comme une table d'énumération) ;
   - paramètres (ref_parameter) typés par une énumération (enum_class), ou listes de codes d'une table du nom du
     paramètre (NO_PACKAGING_…_LIST → ref_packaging) ;
   - tâches planifiées (kis_qrtz_job_details) : classe du job (Standard à la version, ou code du client) et codes
     référencés (refConsumerCode → ref_consumer.code, refSiteCode → ref_site.code…) ;
   - clés de processus ou d'action (menus, événements, paramètres…) : processus Studio déployé, fork du client ou
     Standard à la version (résolution de l'analyse 1.a).

Chaque anomalie est un « risque d'environnement » : la copie est incohérente avec la version testée, le test qui
traverse ce paramétrage échouera pour une raison étrangère au spécifique.
"""
import re

from tools.cucumber_anticipation import ENVIRONMENT, anomaly, split_key

MAX_COLUMNS = 8000
MAX_VALUES_PER_COLUMN = 5
MAX_REFS_PER_COLUMN = 30
BATCH = 40
BATCH_SCAN = 400      # parcours de toutes les colonnes texte : peu de requêtes (une connexion par requête)
_ADDRESS_COLUMN = r"(^|_)(url|uri|host|hostname|endpoint|server|ip|address_ip|queue|topic|broker|printer|smtp|wsdl|" \
                  r"base_url|start_url)(_|$)"
_ADDRESS_VALUE = r"(://|^[a-z0-9][a-z0-9.-]*:[0-9]{2,5}(/|$)|^\\\\)"
# Tables d'historique, de traces, d'exécution et de versions Studio (contrôlées par 1.a) : pas de la configuration
_SKIPPED_TABLES = re.compile(r"(_histo|_history|_log|_log_line|_tbl|_dta|_status|_audit|_version|_version_dps)$|^v_|"
                             r"^kis_(document|history|login|ai_|execution|interaction|qrtz|job_history|custom_|"
                             r"processor|preference|grid_customization|dynamic_translation)|^exc_message_histo|"
                             r"^tms_provider_msg", re.I)
CATEGORIES = (("précolisage", re.compile(r"cub|colis", re.I)), ("WCS", re.compile(r"wcs|mecan|sorter", re.I)),
              ("TMS distant", re.compile(r"tms|carrier|transport", re.I)),
              ("messagerie", re.compile(r"mail|smtp", re.I)),
              ("imprimante", re.compile(r"print|spool|report_exchange|label", re.I)),
              ("file de messages", re.compile(r"queue|topic|jms|amqp|kafka|broker|mq", re.I)),
              ("partenaire de messages", re.compile(r"exc_message|partner", re.I)))
_LOCAL_HOST = re.compile(r"^(localhost|127\.0\.0\.1|0\.0\.0\.0|::1)$", re.I)
_URL_HOST = re.compile(r"^(?:[a-z][\w+.-]*://)?(?:[^@/]*@)?\[?([^/:?#\]\\]+)", re.I)
_UNC_HOST = re.compile(r"^\\\\([^\\]+)")
_COMPONENT_KEY = r"[a-z][a-z0-9-]*/[a-z][a-z0-9-]*-(process|action)-[a-z0-9-]*[a-z0-9]"
_CODE_KEY = re.compile(r"^(ref|wms|tms|exc|kis)([A-Z]\w*?)Code$")
_IDENTIFIER = re.compile(r"^[a-z_][a-z0-9_]*$")


def _rows(query, sql: str, schema: str):
    """Première colonne de chaque ligne ; None si la requête échoue (non vérifié)."""
    try:
        return [str(r.get("code", "")) for r in query(sql, schema)]
    except Exception:  # noqa: BLE001 - table absente ou base illisible
        return None


def _quote(value: str) -> str:
    return "'" + str(value).replace("'", "''") + "'"


def _union(parts: list) -> str:
    return "SELECT code FROM (" + " UNION ALL ".join(parts) + ") u"


def _batched(query, schema: str, parts: list, batch: int = BATCH) -> list:
    """Requêtes groupées par `batch` ; un groupe en erreur est relu partie par partie (colonne illisible ignorée)."""
    out = []
    for index in range(0, len(parts), batch):
        chunk = parts[index:index + batch]
        rows = _rows(query, _union(chunk), schema)
        if rows is None:
            rows = []
            for part in chunk:
                rows += _rows(query, _union([part]), schema) or []
        out += rows
    return out


# ---------------------------------------------------------------------------
# Inventaire du schéma
# ---------------------------------------------------------------------------

class Schema:
    """Colonnes texte, colonnes « code », clés étrangères du schéma de la copie (lus une fois)."""

    def __init__(self, schema: str, query):
        self.schema, self.query = schema, query
        self.text_columns, self.code_tables, self.id_tables, self.foreign = [], set(), set(), set()
        rows = _rows(query, "SELECT c.table_name || '@@' || c.column_name || '@@' || c.data_type AS code FROM "
                            "information_schema.columns c JOIN information_schema.tables t ON t.table_schema = "
                            "c.table_schema AND t.table_name = c.table_name AND t.table_type = 'BASE TABLE' WHERE "
                            "c.table_schema = current_schema() ORDER BY 1", schema)
        self.readable = rows is not None
        for row in rows or []:
            table, column, kind = (row.split("@@") + ["", "", ""])[:3]
            if not table or not column:
                continue
            if column == "code":
                self.code_tables.add(table)
            if column == "id" and kind in ("character varying", "text"):
                self.id_tables.add(table)
            if kind in ("character varying", "text") and not _SKIPPED_TABLES.search(table) \
                    and not column.startswith("custom_"):
                self.text_columns.append((table, column))
        self.text_columns = self.text_columns[:MAX_COLUMNS]
        for row in _rows(query, "SELECT k.conrelid::regclass::text || '@@' || a.attname AS code FROM pg_constraint k "
                                "JOIN pg_attribute a ON a.attrelid = k.conrelid AND a.attnum = ANY(k.conkey) WHERE "
                                "k.contype = 'f' AND k.connamespace = current_schema()::regnamespace", schema) or []:
            table, _, column = row.partition("@@")
            self.foreign.add((table.split(".")[-1].strip('"'), column))

    def codes(self, table: str, values) -> set:
        """Valeurs présentes dans `table`.code parmi `values` ; None si illisible."""
        values = sorted({v for v in values if v})
        if not values or not _IDENTIFIER.match(table):
            return set()
        rows = _rows(self.query, f'SELECT code::text AS code FROM "{table}" WHERE code::text IN '
                                 f'({", ".join(_quote(v) for v in values)})', self.schema)
        return None if rows is None else set(rows)


# ---------------------------------------------------------------------------
# 1. Services externes
# ---------------------------------------------------------------------------

def category(*texts) -> str:
    joined = " ".join(str(t or "") for t in texts)
    return next((label for label, pattern in CATEGORIES if pattern.search(joined)), "autre")


def host_of(value: str) -> str:
    """Hôte d'une adresse (URL, hôte:port, chemin UNC), sans identifiant ni chemin ; '' si illisible."""
    value = (value or "").strip()
    match = _UNC_HOST.match(value) or _URL_HOST.match(value)
    host = match.group(1).strip() if match else ""
    from agent_socle import secret_tools
    return secret_tools.mask_secrets(host)[0] if host else ""


def external_connections(db: Schema) -> list:
    """Branchements externes de la copie : [{categorie, table, colonne, code, hote}] (hôte seul, sans doublon)."""
    columns = [(t, c) for t, c in db.text_columns if re.search(_ADDRESS_COLUMN, c, re.I)]
    parts = []
    for table, column in columns:
        code = "coalesce(\"code\"::text, '')" if table in db.code_tables else "''"
        parts.append(f"(SELECT '{table}@@{column}@@' || {code} || '@@' || \"{column}\" AS code FROM \"{table}\" "
                     f"WHERE \"{column}\" ~* '{_ADDRESS_VALUE}' LIMIT {MAX_VALUES_PER_COLUMN})")
    found, seen = [], set()
    for item in _batched(db.query, db.schema, parts):
        table, column, code, value = (item.split("@@", 3) + ["", "", "", ""])[:4]
        host = host_of(value)
        key = (table, column, code, host)
        if not host or _LOCAL_HOST.match(host) or key in seen:
            continue
        seen.add(key)
        found.append({"categorie": category(table, column, code), "table": table, "colonne": column, "code": code,
                      "hote": host})
    return found


def check_external(connections: list) -> list:
    from tools.cucumber_copied_config import EXTERNAL_CONNECTIONS
    known = {table: action for _kind, table, _column, action in EXTERNAL_CONNECTIONS}
    return [anomaly(f"branchement vers un service externe ({c['categorie']}"
                    + (f" « {c['code']} »" if c.get("code") else "") + f") : {c['hote']}",
                    "", f"{c['table']}.{c['colonne']}", 0,
                    "tout scénario qui déclenche cet échange appelle le système du client ou échoue s'il est "
                    "injoignable depuis l'environnement de test",
                    known.get(c["table"]) or "neutraliser dans l'init de la configuration copiée (adresse de test, "
                                             "partenaire ou échange désactivé) ou monter le service en test",
                    ENVIRONMENT, "configuration copiée", c["hote"])
            for c in connections]


# ---------------------------------------------------------------------------
# 2. Paramètres pointant vers des objets absents
# ---------------------------------------------------------------------------

def check_enum_columns(db: Schema) -> list:
    """Colonnes nommées comme une table d'énumération (id texte) sans clé étrangère : valeurs absentes de la table."""
    columns = [(t, c) for t, c in db.text_columns if c in db.id_tables and c != t and (t, c) not in db.foreign]
    parts = [f"(SELECT DISTINCT '{t}@@{c}@@' || \"{c}\" AS code FROM \"{t}\" WHERE coalesce(\"{c}\", '') <> '' AND "
             f"\"{c}\"::text NOT IN (SELECT id::text FROM \"{c}\") LIMIT {MAX_VALUES_PER_COLUMN})" for t, c in columns]
    out = []
    for item in _batched(db.query, db.schema, parts):
        table, column, value = (item.split("@@", 2) + ["", "", ""])[:3]
        out.append(anomaly(f"valeur « {value} » de {table}.{column} absente de l'énumération {column}", "",
                           f"{table}.{column}", 0,
                           "le paramétrage qui la lit échoue ou est ignoré : scénario faux ou en erreur",
                           f"corriger la valeur dans la copie (init de la configuration copiée) ou ajouter « {value} » "
                           f"à {column}", ENVIRONMENT, "aucune clé étrangère sur cette colonne", value))
    return out


def _tokens_tables(name: str, db: Schema) -> list:
    """Tables à colonne code nommées d'après un mot du paramètre (NO_PACKAGING_…_LIST → ref_packaging)."""
    words = [w for w in re.split(r"[^a-z0-9]+", name.lower()) if len(w) > 3]
    return sorted({t for t in db.code_tables for w in words if re.fullmatch(rf"[a-z]+_{re.escape(w)}", t)})


def check_parameters(db: Schema, declarations) -> tuple:
    """Paramètres (ref_parameter) : énumération typée (enum_class) ou liste de codes. (anomalies, non vérifiés)."""
    rows = _rows(db.query, "SELECT p.ref_enum_parameter || '@@' || coalesce(e.enum_class, '') || '@@' || "
                           "p.string_value AS code FROM ref_parameter p JOIN ref_enum_parameter e ON e.id = "
                           "p.ref_enum_parameter WHERE coalesce(p.string_value, '') <> '' AND e.datatype = 'STRING'",
                 db.schema)
    anomalies, unverified = [], {}
    for row in rows or []:
        name, enum_class, value = (row.split("@@", 2) + ["", "", ""])[:3]
        where = f"ref_parameter {name}"
        if enum_class:
            declared = declarations.enum_values(enum_class) if declarations is not None else None
            if declared is None:
                unverified[where] = (f"énumération {enum_class.rsplit('.', 1)[-1]} sans table dans la base "
                                    f"(énumération Java) ou illisible")
            elif value not in declared:
                anomalies.append(anomaly(f"paramètre {name} = « {value} » absent de l'énumération "
                                         f"{enum_class.rsplit('.', 1)[-1]}", "", where, 0,
                                         "le Standard rejette ou ignore ce paramètre : comportement imprévisible",
                                         "corriger la valeur du paramètre dans la copie", ENVIRONMENT,
                                         f"valeurs déclarées : {', '.join(sorted(declared)[:8])}", value))
            continue
        items = [v.strip() for v in value.split(",") if v.strip()]
        if len(items) < 2 and not name.upper().endswith("_LIST"):
            continue
        tables = _tokens_tables(name, db)
        if not tables:
            unverified[where] = "liste de codes sans table identifiable"
            continue
        present, readable = set(), False
        for table in tables:
            found = db.codes(table, items)
            if found is not None:
                readable = True
                present |= found
        missing = [v for v in items if v not in present]
        if not readable:
            unverified[where] = f"{', '.join(tables)} illisible(s)"
        elif missing:
            anomalies.append(anomaly(f"paramètre {name} : code(s) {', '.join(missing)} absent(s) de "
                                     f"{' / '.join(tables)}", "", where, 0,
                                     "le traitement qui lit cette liste ne retrouve pas ces objets",
                                     "corriger la liste dans la copie ou créer les objets", ENVIRONMENT,
                                     f"table(s) {', '.join(tables)}", ", ".join(missing)))
    return anomalies, unverified


def serialized_strings(blob: bytes) -> list:
    """
    Chaînes d'un objet Java sérialisé (JobDataMap de Quartz), dans l'ordre : [(début, fin, texte)]. TC_STRING (0x74,
    longueur sur 2 octets) et TC_LONGSTRING (0x7C, longueur sur 8 octets), texte UTF-8 imprimable.
    """
    out, index, size = [], 0, len(blob)
    while index < size - 3:
        marker = blob[index]
        width = 2 if marker == 0x74 else 8 if marker == 0x7C else 0
        if width:
            length = int.from_bytes(blob[index + 1:index + 1 + width], "big")
            start, end = index + 1 + width, index + 1 + width + length
            if 0 < length < 100000 and end <= size:
                try:
                    text = blob[start:end].decode("utf-8")
                except UnicodeDecodeError:
                    text = ""
                if text and text.isprintable():
                    out.append((index, end, text))
                    index = end
                    continue
        index += 1
    return out


def job_values(blob: bytes) -> dict:
    """{clé: valeur} des entrées texte d'un JobDataMap (la valeur suit immédiatement la clé)."""
    strings = serialized_strings(blob)
    out, index = {}, 0
    while index < len(strings) - 1:
        (_start, end, key), (start, _end, value) = strings[index], strings[index + 1]
        if start == end:            # clé puis valeur texte : couple consommé
            out.setdefault(key, value)
            index += 2
        else:                       # valeur nulle ou objet : clé seule
            index += 1
    return out


def _snake(name: str) -> str:
    return re.sub(r"(?<!^)(?=[A-Z])", "_", name).lower()


def check_jobs(db: Schema, class_exists, component_exists, identifiers=frozenset()) -> tuple:
    """
    Tâches planifiées : classe du job et codes référencés. (anomalies, non vérifiés). Une valeur d'une clé « …Code »
    ou un code d'objet de la copie (`identifiers`) n'est pas une clé de composant, même s'il en a la forme.
    """
    rows = _rows(db.query, "SELECT job_name || '@@' || encode(job_data, 'hex') AS code FROM kis_qrtz_job_details "
                           "WHERE job_data IS NOT NULL", db.schema)
    anomalies, unverified = [], {}
    for row in rows or []:
        name, _, data = row.partition("@@")
        try:
            values = job_values(bytes.fromhex(data.strip()))
        except ValueError:
            continue
        where = f"tâche planifiée « {name} »"
        job_type = values.get("jobType", "")
        if job_type and class_exists is not None:
            exists = class_exists(job_type)
            if exists is None:
                unverified[f"{where} ({job_type})"] = "classe hors des jars du Standard lus et du code du client"
            elif not exists:
                anomalies.append(anomaly(f"classe {job_type} de la tâche planifiée « {name} » introuvable", "",
                                         where, 0, "la tâche échoue à chaque déclenchement",
                                         "désactiver la tâche dans l'init de la configuration copiée ou corriger sa "
                                         "classe", ENVIRONMENT, "jobType du JobDataMap (kis_qrtz_job_details)",
                                         job_type))
        for key, value in values.items():
            match = _CODE_KEY.match(key)
            table = f"{match.group(1)}_{_snake(match.group(2))}" if match else ""
            if not table or table not in db.code_tables or not value:
                continue
            present = db.codes(table, [value])
            if present is None:
                unverified[f"{where} ({key})"] = f"{table} illisible"
            elif value not in present:
                anomalies.append(anomaly(f"{key} « {value} » de la tâche planifiée « {name} » absent de "
                                         f"{table}", "", where, 0, "la tâche échoue ou ne traite rien",
                                         f"corriger la tâche ou créer « {value} » dans {table}", ENVIRONMENT,
                                         "JobDataMap (kis_qrtz_job_details)", value))
        for entry, value in values.items():
            if _CODE_KEY.match(entry):
                continue
            for key in re.findall(rf"\b({_COMPONENT_KEY})\b", value):
                key = key[0] if isinstance(key, tuple) else key
                if key not in identifiers:
                    anomalies += _component_anomaly(key, where, component_exists, unverified)
    return anomalies, unverified


def _component_anomaly(key: str, where: str, component_exists, unverified: dict) -> list:
    if component_exists is None:
        return []
    state, proof = component_exists(key)
    if state == "absent":
        return [anomaly(f"composant {key} référencé par {where} absent de la version testée", key, where, 0,
                        "l'appel échoue (« component not found ») : le scénario qui passe par ce paramétrage échoue",
                        "déployer le composant (Studio ou fork) ou corriger la référence dans la copie", ENVIRONMENT,
                        proof, key)]
    if state != "ok":
        unverified.setdefault(key, proof or "composant non résolu")
    return []


def component_references(db: Schema) -> list:
    """[(table, colonne, clé)] des clés de processus / d'action trouvées dans les colonnes texte de la configuration."""
    parts = [f"(SELECT DISTINCT '{t}@@{c}@@' || (regexp_matches(\"{c}\", '({_COMPONENT_KEY})', 'g'))[1] AS code "
             f"FROM \"{t}\" WHERE \"{c}\" ~ '{_COMPONENT_KEY}' LIMIT {MAX_REFS_PER_COLUMN})" for t, c in db.text_columns]
    out = []
    for item in _batched(db.query, db.schema, parts, BATCH_SCAN):
        table, column, key = (item.split("@@", 2) + ["", "", ""])[:3]
        if key and split_key(key):
            out.append((table, column, key))
    return out


def object_codes(references: list) -> set:
    """
    Clés trouvées dans une colonne « code » : identifiants d'objets de la copie nommés comme des composants (ex. code
    d'un consommateur « a-sis/ref-activityreport-process-send-…-to-lmconnect », dupliqué sous n'importe quel code),
    jamais des appels de composant ; leurs mentions ailleurs (profils, tâches) non plus.
    """
    return {key for _table, column, key in references if column == "code"}


def check_component_references(references: list, component_exists, identifiers=frozenset()) -> tuple:
    anomalies, unverified, seen = [], {}, set()
    for table, column, key in references:
        if key in identifiers or (table, column, key) in seen:
            continue
        seen.add((table, column, key))
        anomalies += _component_anomaly(key, f"{table}.{column}", component_exists, unverified)
    return anomalies, unverified


# ---------------------------------------------------------------------------
# Analyse complète
# ---------------------------------------------------------------------------

def analyse_config(schema: str, query, declarations=None, class_exists=None, component_exists=None) -> dict:
    """
    Analyse 1.c de la copie de référence. `query(sql, schema)` → [{"code"}] sans limite de lignes ni de longueur ;
    `class_exists(fqn)` → True / False / None ; `component_exists(clé)` → (ok | absent | non_verifie, preuve).
    Renvoie {anomalies, non_verifies, branchements, controles}.
    """
    db = Schema(schema, query)
    if not db.readable:
        return {"anomalies": [], "non_verifies": {"configuration": f"schéma {schema} illisible"},
                "branchements": [], "controles": 0}
    connections = external_connections(db)
    anomalies, unverified = check_external(connections), {}
    anomalies += check_enum_columns(db)
    references = component_references(db)
    identifiers = object_codes(references)
    for found, missing in (check_parameters(db, declarations),
                           check_jobs(db, class_exists, component_exists, identifiers),
                           check_component_references(references, component_exists, identifiers)):
        anomalies += found
        unverified.update(missing)
    return {"anomalies": anomalies, "non_verifies": unverified, "branchements": connections,
            "controles": len(db.text_columns)}
