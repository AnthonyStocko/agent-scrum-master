"""
Règles métier du client dans la conception des tests (axe B), 0 token, tous clients, sans valeur en dur.

Pour chaque effet attendu par un scénario (besoin d'une UP, d'une OP, d'une UE…) :
1. Cas réel de référence : objets de la base de référence (copie restaurée, ou état après init) qui présentent cet
   effet, et leurs caractéristiques déterminantes — colonnes lues par les règles concernées et dimensions de
   configuration de l'entité (secteur, emballage…), comparées aux objets SANS l'effet (valeurs jamais observées
   avec l'effet). Aucun cas réel : signalé avant d'écrire.
2. Règles concernées (kis_business_rule → version active) : expressions lues dans l'ordre (règles du client, puis
   règles par défaut, puis « otherwise ») ; conditions simples (chemin comparé à une valeur d'énumération ou à un
   code, conjonctions, disjonctions) évaluées de façon déterministe, les autres citées pour vérification par Claude.
   Sens d'un résultat : profil de kis_profile_config (needToAssign / wmsEnumNeedId) ; à défaut, « otherwise » = pas
   d'effet, et valeur d'une expression d'origine conditionnée sur un seul besoin = ce besoin.
3. Données prévues : codes du test reconnus dans les dimensions de l'entité (secteur, emballage…) ; caractéristique
   non écrite par le test déduite de la copie quand tous les objets de la même dimension la partagent (ex. secteur →
   mécanisation). Règle qui refuse l'effet, ou valeur jamais observée avec l'effet : contradiction, avant tout run.
"""
import json
import re

# Entités du modèle du Standard dont un besoin est un effet attendu : libellé du step → (table, identifiant, table des
# besoins, variable des règles)
ENTITIES = (
    (re.compile(r"\bUP\b|unit[ée] de pr[ée]paration", re.I),
     ("UP", "wms_preparation_unit", "wms_preparation_unit_id", "wms_prep_unit_need", "wmsPreparationUnit")),
    (re.compile(r"\bUE\b|unit[ée] d'exp[ée]dition", re.I),
     ("UE", "wms_dispatch_unit", "wms_dispatch_unit_id", "wms_disp_unit_need", "wmsDispatchUnit")),
    (re.compile(r"\bOP\b|ordre de pr[ée]paration", re.I),
     ("OP", "wms_preparation_order", "wms_preparation_order_id", "wms_preparation_order_need", "wmsPreparationOrder")),
)
NEED_COLUMN = "wmsenumneed.value.id"
STATUS_COLUMNS = ("status.value.id", "status")
MAX_DIMENSION_ROWS = 2000
MAX_COMBINATIONS = 5000
MIN_REAL_CASES = 10           # écart statistique bloquant à partir de ce nombre de cas réels
MIN_MATCHED_VALUE = 3          # code du test rattaché à une caractéristique : 3 caractères au moins (pas « 01 », « 1 »)
REFERENCE_MARGIN_HOURS = 3     # marge sur la date de la copie (fuseau du serveur)
CATEGORY = "règle métier"
LESSON = ("chez un client, les règles métier peuvent différer du Standard : partir d'un cas réel de la copie qui "
          "présente l'effet attendu (ses caractéristiques déterminantes), jamais des données d'un test du Standard")
_STEP = re.compile(r"^\s*(?:Given|When|Then|And|But|\*|Soit|Quand|Alors|Et|Mais)\s+(.*?)\s*$")
_SCENARIO = re.compile(r"^\s*(?:Scenario|Scenario Outline|Scénario|Plan du scénario)\s*:\s*(.*)$", re.I)
_IDENTIFIER = re.compile(r"^[a-z_][a-z0-9_]*$")


def _rows(query, sql: str, schema: str):
    try:
        return [str(r.get("code", "")) for r in query(sql, schema)]
    except Exception:  # noqa: BLE001 - table absente ou base illisible : non vérifié
        return None


def _quote(value: str) -> str:
    return "'" + str(value).replace("'", "''") + "'"


def snake(name: str) -> str:
    return re.sub(r"(?<!^)(?=[A-Z])", "_", name).lower()


# ---------------------------------------------------------------------------
# Effets attendus par les scénarios
# ---------------------------------------------------------------------------

def _cells(line: str) -> list:
    return [c.strip() for c in line.strip().strip("|").split("|")]


def expected_effects(text: str) -> list:
    """
    Besoins attendus par les scénarios d'une feature : [{scenario, entite, besoin, statut, ligne, step}] pour chaque
    ligne de table d'un step « … besoins … » dont la colonne wmsEnumNeed est renseignée et le nombre attendu ≥ 1
    (un nombre 0 vérifie une absence : pas un effet à obtenir).
    """
    lines = (text or "").splitlines()
    out, scenario, index, phase = [], "", 0, "precondition"
    while index < len(lines):
        match = _SCENARIO.match(lines[index])
        if match:
            scenario, phase = match.group(1).strip(), "precondition"
        if re.match(r"^\s*(When|Quand)\b", lines[index]):
            phase = "resultat"
        step = _STEP.match(lines[index])
        index += 1
        if not step or "besoin" not in step.group(1).lower():
            continue
        entity = next((spec for pattern, spec in ENTITIES if pattern.search(step.group(1))), None)
        table = []
        while index < len(lines) and lines[index].strip().startswith(("|", "#")):
            if lines[index].strip().startswith("|"):
                table.append((index + 1, _cells(lines[index])))
            index += 1
        if not entity or len(table) < 2:
            continue
        header = [h.lower() for h in table[0][1]]
        if NEED_COLUMN not in header:
            continue
        for number, cells in table[1:]:
            row = dict(zip(header, cells))
            count = row.get("x", "1")
            if not row.get(NEED_COLUMN) or (count.isdigit() and int(count) == 0):
                continue
            status = next((row[c] for c in STATUS_COLUMNS if row.get(c)), "")
            out.append({"scenario": scenario, "entite": entity, "besoin": row[NEED_COLUMN], "statut": status,
                        "ligne": number, "step": step.group(1), "phase": phase})
    return out


def feature_values(text: str) -> set:
    """Valeurs citées par une feature : cellules des tables (listes {a, b} éclatées) et chaînes entre guillemets."""
    values = set()
    for line in (text or "").splitlines():
        stripped = line.strip()
        if stripped.startswith("|"):
            for cell in _cells(stripped):
                for part in cell.strip("{}").split(","):
                    if part.strip():
                        values.add(part.strip())
        elif not stripped.startswith("#"):
            values.update(re.findall(r'"([^"]{1,80})"', stripped))
    return values


# ---------------------------------------------------------------------------
# Règles du moteur de règles
# ---------------------------------------------------------------------------

def active_rules(schema: str, query) -> list:
    """[{cle, nom, version, regle (JSON)}] des versions actives (kis_business_rule → kis_business_rule_version)."""
    rows = _rows(query, "SELECT v.partial_key || '@@' || coalesce(v.name, '') || '@@' || v.version || '@@' || v.rules "
                        "AS code FROM kis_business_rule b JOIN kis_business_rule_version v ON v.id = "
                        "b.business_rule_version WHERE v.rules IS NOT NULL", schema)
    out = []
    for row in rows or []:
        key, name, version, text = (row.split("@@", 3) + ["", "", "", ""])[:4]
        try:
            out.append({"cle": key, "nom": name, "version": version, "regle": json.loads(text)})
        except ValueError:
            continue
    return out


def expressions(rule: dict) -> list:
    """Expressions dans l'ordre d'évaluation : [{condition, description, valeur, origine}] (désactivées exclues)."""
    out = []
    for origin, block in (("client", "userRule"), ("Standard", "defaultRule")):
        for expr in ((rule.get(block) or {}).get("expressions") or []):
            if not expr.get("disabled"):
                out.append({"condition": str(expr.get("condition") or "").strip(),
                            "description": str(expr.get("conditionDescription") or "").strip(),
                            "valeur": str(expr.get("value") or ""), "origine": origin})
    return out


def otherwise(rule: dict) -> str:
    return str(((rule.get("defaultRule") or {}).get("otherwise")) or "")


def concerns(rule: dict, need: str, variable: str) -> bool:
    """La règle évalue ce besoin (wmsEnumNeed comparé à lui) sur cette entité."""
    text = json.dumps(rule)
    return bool(re.search(rf"WmsEnumNeed\.{re.escape(need)}\(\)|valueOf\(\\?'{re.escape(need)}\\?'\)", text)) and \
        variable in text


def profiles(schema: str, query) -> dict:
    """{id: {nom, parametres}} de kis_profile_config."""
    rows = _rows(query, "SELECT profile_config_id::text || '@@' || coalesce(name, '') || '@@' || coalesce(config, '') "
                        "AS code FROM kis_profile_config", schema)
    out = {}
    for row in rows or []:
        pid, name, config = (row.split("@@", 2) + ["", "", ""])[:3]
        try:
            parameters = (json.loads(config) or {}).get("parameters") or {}
        except ValueError:
            parameters = {}
        out[pid] = {"nom": name, "parametres": parameters}
    return out


def outcome(rule: dict, value: str, need: str, known: dict) -> tuple:
    """
    (effet obtenu : True / False / None, explication) d'une valeur de résultat pour ce besoin. Profil connu :
    needToAssign et wmsEnumNeedId ; « otherwise » de la règle : pas de besoin ; valeur d'une expression d'origine
    du Standard conditionnée sur ce seul besoin : ce besoin.
    """
    profile = known.get(value)
    if profile:
        parameters = profile["parametres"]
        assign = parameters.get("needToAssign")
        target = parameters.get("wmsEnumNeedId")
        if assign is False:
            return False, f"profil « {profile['nom']} » : needToAssign = false"
        if assign is True:
            same = not target or str(target) == need
            return same, f"profil « {profile['nom']} » : besoin {target or need} attribué"
    if value and value == otherwise(rule):
        return False, "résultat « otherwise » de la règle (aucun besoin)"
    for expr in expressions(rule):
        needs = set(re.findall(r"WmsEnumNeed\.(\w+)\(\)|valueOf\('(\w+)'\)", expr["condition"]))
        needs = {a or b for a, b in needs}
        if expr["origine"] == "Standard" and expr["valeur"] == value and needs == {need}:
            return True, f"profil du Standard des expressions sur le besoin {need}"
    return None, f"profil {value} de sens inconnu"


# ---------------------------------------------------------------------------
# Conditions : analyse et évaluation à trois valeurs (True / False / None = inconnu ou complexe)
# ---------------------------------------------------------------------------

def _split_top(text: str, operator: str) -> list:
    parts, depth, start, index = [], 0, 0, 0
    while index < len(text):
        char = text[index]
        if char in "([{":
            depth += 1
        elif char in ")]}":
            depth -= 1
        elif depth == 0 and text.startswith(operator, index):
            parts.append(text[start:index])
            start = index + len(operator)
            index = start
            continue
        index += 1
    parts.append(text[start:])
    return [p.strip() for p in parts]


def _strip_parens(text: str) -> str:
    text = text.strip()
    while text.startswith("(") and text.endswith(")"):
        depth = 0
        for index, char in enumerate(text):
            depth += char == "("
            depth -= char == ")"
            if depth == 0 and index < len(text) - 1:
                return text
        text = text[1:-1].strip()
    return text


_ENUM_VALUE = r"\$[\w$]+\.(\w+)\(\)|\$[\w$]+\.valueOf\('(\w+)'\)"
_COMPARE = re.compile(rf"^([\w.?]+)\s*(==|!=)\s*(?:{_ENUM_VALUE}|null)$")
_EQUALS = re.compile(r"^(!?)([\w.?]+)\.equals\(\"([^\"]*)\"\)$")


def atoms(condition: str) -> list:
    """Atomes simples d'une condition : [(chemin, opérateur ==/!=, valeur ou None)] ; atome complexe : (texte, '?', None)."""
    out = []
    for part in _split_top(_strip_parens(condition), "&&"):
        part = _strip_parens(part)
        if len(_split_top(part, "||")) > 1:
            for sub in _split_top(part, "||"):
                out += atoms(sub)
            continue
        match = _COMPARE.match(part)
        if match:
            out.append((match.group(1).replace("?", ""), match.group(2), match.group(3) or match.group(4)))
            continue
        match = _EQUALS.match(part)
        if match:
            out.append((match.group(2).replace("?", ""), "!=" if match.group(1) else "==", match.group(3)))
            continue
        out.append((part, "?", None))
    return out


def evaluate(condition: str, facts: dict):
    """True / False / None (inconnu) ; `facts` : {chemin: valeur} (wmsEnumNeed compris)."""
    def atom(part):
        part = _strip_parens(part)
        alternatives = _split_top(part, "||")
        if len(alternatives) > 1:
            values = [conjunction(a) for a in alternatives]
            return True if True in values else None if None in values else False
        match = _COMPARE.match(part)
        if match:
            path, operator, value = match.group(1).replace("?", ""), match.group(2), match.group(3) or match.group(4)
        else:
            match = _EQUALS.match(part)
            if not match:
                return None
            path, operator, value = match.group(2).replace("?", ""), "!=" if match.group(1) else "==", match.group(3)
        if path not in facts or facts[path] is None:
            return None
        equal = str(facts[path]) == str(value)
        return equal if operator == "==" else not equal

    def conjunction(text):
        values = [atom(p) for p in _split_top(_strip_parens(text), "&&")]
        return False if False in values else None if None in values else True
    return conjunction(condition)


def decide(rule: dict, need: str, facts: dict, known: dict) -> dict:
    """
    Déroule la règle pour ce besoin : {effet: True/False/None, expression (la première vraie), indecises (expressions
    inconnues rencontrées avant), explication}.
    """
    pending = []
    for expr in expressions(rule):
        value = evaluate(expr["condition"], {**facts, "wmsEnumNeed": need})
        if value is None:
            if evaluate(expr["condition"], {"wmsEnumNeed": need}) is not False:
                pending.append(expr)
            continue
        if value:
            effect, why = outcome(rule, expr["valeur"], need, known)
            return {"effet": effect if not pending else None, "expression": expr, "indecises": pending,
                    "explication": why}
    effect, why = outcome(rule, otherwise(rule), need, known)
    return {"effet": effect if not pending else None, "expression": None, "indecises": pending,
            "explication": why}


def refusing(rule: dict, need: str, known: dict) -> list:
    """Expressions qui, pour ce besoin, mènent à « pas d'effet » : [{condition, description, origine}]."""
    out = []
    for expr in expressions(rule):
        if evaluate(expr["condition"], {"wmsEnumNeed": need}) is False:
            continue
        effect, _why = outcome(rule, expr["valeur"], need, known)
        if effect is False:
            out.append(expr)
    return out


# ---------------------------------------------------------------------------
# Cas réels de référence
# ---------------------------------------------------------------------------

class Entity:
    """Caractéristiques d'une entité lisibles dans la base : colonnes simples et dimensions (table à code)."""

    def __init__(self, spec: tuple, schema: str, query, since: str = ""):
        self.label, self.table, self.id, self.need_table, self.variable = spec
        self.schema, self.query, self.since = schema, query, since
        rows = _rows(query, f"SELECT column_name || '@@' || data_type AS code FROM information_schema.columns WHERE "
                            f"table_schema = current_schema() AND table_name = '{self.table}'", schema)
        self.readable = bool(rows)
        self.columns = dict(r.split("@@", 1) for r in rows or [] if "@@" in r)
        self.dimensions = {}   # colonne FK → table référencée (à colonne code, de configuration)
        for row in _rows(query, "SELECT a.attname || '@@' || k.confrelid::regclass::text AS code FROM pg_constraint k "
                                "JOIN pg_attribute a ON a.attrelid = k.conrelid AND a.attnum = ANY(k.conkey) WHERE "
                                f"k.contype = 'f' AND k.conrelid = '{self.table}'::regclass", schema) or []:
            column, _, target = row.partition("@@")
            self.dimensions[column] = target.split(".")[-1].strip('"')
        small = set(_rows(query, "SELECT c.relname AS code FROM pg_class c JOIN information_schema.columns i ON "
                                 "i.table_schema = current_schema() AND i.table_name = c.relname AND i.column_name = "
                                 f"'code' WHERE c.relnamespace = current_schema()::regnamespace AND c.relkind = 'r' "
                                 f"AND c.reltuples < {MAX_DIMENSION_ROWS}", schema) or [])
        self.dimensions = {c: t for c, t in self.dimensions.items() if t in small and _IDENTIFIER.match(t)}
        # Dimensions de configuration (caractéristiques comparées) : ni donnée métier de la copie (OP, UE, avis
        # d'expédition…), ni table à cycle de vie (statut) ; les autres restent lisibles par les chemins des règles
        from tools.cucumber_copied_config import business_tables
        business = {table for _label, table in business_tables()}
        lifecycle = set(_rows(query, "SELECT table_name AS code FROM information_schema.columns WHERE table_schema = "
                                     "current_schema() AND column_name = 'status'", schema) or [])
        self.configuration = {c: t for c, t in self.dimensions.items() if t not in business and t not in lifecycle}

    def column_of(self, path: str) -> tuple:
        """(expression SQL, libellé) d'un chemin de règle sur cette entité ; ('', '') s'il n'est pas lisible."""
        parts = path.split(".")
        if not parts or parts[0] != self.variable or len(parts) < 2:
            return "", ""
        if len(parts) == 2 and snake(parts[1]) in self.columns:
            return f"e.\"{snake(parts[1])}\"::text", snake(parts[1])
        if len(parts) == 3 and parts[2] == "code":
            column = snake(parts[1]) + "_id"
            if column in self.dimensions:
                return self._dimension_sql(column), self.dimensions[column]
        return "", ""

    def _dimension_sql(self, column: str) -> str:
        table = self.dimensions[column]
        key = f"{table}_id"
        return f"(SELECT d.code::text FROM \"{table}\" d WHERE d.\"{key}\" = e.\"{column}\")"

    def characteristics(self, paths: list) -> list:
        """[(libellé, expression SQL, chemin de règle ou '')] : chemins des règles lisibles, puis dimensions."""
        out, seen = [], set()
        for path in paths:
            sql, label = self.column_of(path)
            if sql and label not in seen:
                out.append((label, sql, path))
                seen.add(label)
        for column, table in sorted(self.configuration.items()):
            if table not in seen and f"{table}_id" in self._columns_of(table):
                out.append((table, self._dimension_sql(column), ""))
                seen.add(table)
        return out

    def _columns_of(self, table: str) -> set:
        if not hasattr(self, "_cache"):
            self._cache = {}
        if table not in self._cache:
            rows = _rows(self.query, "SELECT column_name AS code FROM information_schema.columns WHERE table_schema = "
                                     f"current_schema() AND table_name = '{table}'", self.schema)
            self._cache[table] = set(rows or [])
        return self._cache[table]

    def combinations_sql(self, characteristics: list, need: str) -> str:
        values = " || '@@' || ".join(f"coalesce({sql}, '∅')" for _label, sql, _path in characteristics)
        effect = (f"(EXISTS (SELECT 1 FROM \"{self.need_table}\" n WHERE n.\"{self.id}\" = e.\"{self.id}\" AND "
                  f"n.wms_enum_need = {_quote(need)}))::text")
        where = ""
        if self.since and "created_date" in self.columns:
            # Objets créés après la copie de référence : ceux des runs, pas des cas réels du client
            where = (f"WHERE e.created_date <= {_quote(self.since[:19].replace('T', ' '))}::timestamp + interval "
                     f"'{REFERENCE_MARGIN_HOURS} hours' ")
        return (f"SELECT {values} || '@@' || {effect} || '@@' || count(*) AS code FROM \"{self.table}\" e {where}"
                f"GROUP BY {values}, {effect} ORDER BY count(*) DESC LIMIT {MAX_COMBINATIONS}")

    def combinations(self, characteristics: list, need: str):
        rows = _rows(self.query, self.combinations_sql(characteristics, need), self.schema)
        if rows is None:
            return None
        out = []
        for row in rows:
            parts = row.split("@@")
            if len(parts) != len(characteristics) + 2:
                continue
            out.append({"valeurs": dict(zip([c[0] for c in characteristics], parts[:-2])),
                        "effet": parts[-2] == "true", "nombre": int(parts[-1]) if parts[-1].isdigit() else 0})
        return out


def determinants(characteristics: list, combos: list) -> list:
    """
    [{caracteristique, avec: {valeur: n}, jamais: {valeur: n}}] : valeurs observées avec l'effet, et valeurs présentes
    sans l'effet mais jamais avec (ce qui distingue les cas réels), les plus discriminantes d'abord.
    """
    out = []
    for label, _sql, path in characteristics:
        with_effect, without = {}, {}
        for combo in combos:
            target = with_effect if combo["effet"] else without
            value = combo["valeurs"][label]
            target[value] = target.get(value, 0) + combo["nombre"]
        never = {v: n for v, n in without.items() if v not in with_effect}
        if never:
            out.append({"caracteristique": label, "chemin": path,
                        "avec": dict(sorted(with_effect.items(), key=lambda kv: -kv[1])),
                        "jamais": dict(sorted(never.items(), key=lambda kv: -kv[1])),
                        "poids": sum(never.values())})
    return sorted(out, key=lambda d: (not d["chemin"], -d["poids"]))


def planned_facts(characteristics: list, combos: list, values: set) -> dict:
    """
    {caracteristique: (valeur, preuve)} des données prévues : code du test présent dans une caractéristique, puis
    caractéristique déduite quand tous les objets de cette valeur la partagent (dépendance observée dans la copie).
    """
    known = {}
    for label, _sql, _path in characteristics:
        observed = {c["valeurs"][label] for c in combos}
        hits = sorted(v for v in values if v in observed and len(v) >= MIN_MATCHED_VALUE)
        if len(hits) == 1:
            known[label] = (hits[0], "valeur écrite dans le test")
    for label, value in list(known.items()):
        same = [c for c in combos if c["valeurs"][label] == value[0]]
        total = sum(c["nombre"] for c in same)
        for other, _sql, _path in characteristics:
            if other in known:
                continue
            seen = {c["valeurs"][other] for c in same}
            if len(seen) == 1 and total:
                known[other] = (seen.pop(), f"tous les objets ({total}) de {label} « {value[0]} » dans la copie")
    return known


# ---------------------------------------------------------------------------
# Analyse d'un effet
# ---------------------------------------------------------------------------

def analyse_effect(effect: dict, schema: str, query, rules: list = None, known: dict = None,
                   values: set = frozenset(), since: str = "") -> dict:
    """
    {effet, cas_reels, nombre, requete, determinants, regles, prevu, contradictions, non_verifie}. Contradiction :
    règle qui refuse l'effet avec les données prévues, ou donnée prévue jamais observée avec l'effet.
    """
    label, need = effect["entite"][0], effect["besoin"]
    entity = Entity(effect["entite"], schema, query, since)
    result = {"effet": f"besoin {need} sur {label}", "entite": label, "besoin": need, "cas_reels": [], "nombre": 0,
              "requete": "", "determinants": [], "regles": [], "prevu": {}, "contradictions": [],
              "avertissements": [], "non_verifie": "", "phase": effect.get("phase", "precondition")}
    if not entity.readable:
        result["non_verifie"] = f"table {entity.table} illisible"
        return result
    rules = active_rules(schema, query) if rules is None else rules
    known = profiles(schema, query) if known is None else known
    concerned = [r for r in rules if concerns(r["regle"], need, entity.variable)]
    paths = []
    for rule in concerned:
        for expr in expressions(rule["regle"]):
            paths += [p for p, op, _v in atoms(expr["condition"]) if op != "?" and p.startswith(entity.variable + ".")]
    characteristics = entity.characteristics(paths)
    combos = entity.combinations(characteristics, need) if characteristics else None
    if combos is None:
        result["non_verifie"] = f"caractéristiques de {entity.table} illisibles"
        return result
    result["requete"] = entity.combinations_sql(characteristics, need)
    real = [c for c in combos if c["effet"]]
    result["nombre"] = sum(c["nombre"] for c in real)
    result["cas_reels"] = real[:6]
    result["determinants"] = determinants(characteristics, combos)
    planned = planned_facts(characteristics, combos, set(values))
    result["prevu"] = {k: v for k, v in planned.items()}
    facts = {path: planned[label][0] for label, _sql, path in characteristics if path and label in planned}
    for rule in concerned:
        decision = decide(rule["regle"], need, facts, known)
        result["regles"].append({"cle": rule["cle"], "nom": rule["nom"], "version": rule["version"],
                                 "refus": [e for e in refusing(rule["regle"], need, known)],
                                 "decision": decision})
        expr = decision["expression"]
        if decision["effet"] is False and expr is not None:
            result["contradictions"].append(
                f"règle {rule['nom'] or rule['cle']} (version active {rule['version']}) : avec les données prévues "
                f"({', '.join(f'{k} = {v}' for k, v in sorted(facts.items()))}), la condition « {expr['condition']} » "
                f"({expr['description']}) est vraie → {decision['explication']}")
    if not result["nombre"]:
        missing = (f"aucun cas réel : aucune {label} de la base de référence ne porte le besoin {need} (effet "
                   f"peut-être jamais obtenu chez le client, ou spécifique non utilisé)")
        # Précondition (avant l'action testée) : le test ne peut pas l'obtenir sans cas réel ; effet produit par
        # l'action testée (le spécifique lui-même) : signalé seulement
        (result["contradictions"] if effect.get("phase", "precondition") == "precondition"
         else result["avertissements"]).append(missing)
    # Écart statistique (valeur prévue jamais observée avec l'effet) : contradiction pour une précondition appuyée sur
    # assez de cas réels ; sinon (effet produit par l'action testée, cas trop rares) avertissement
    strong = result["phase"] == "precondition" and result["nombre"] >= MIN_REAL_CASES
    for item in result["determinants"]:
        value = planned.get(item["caracteristique"])
        if value and value[0] in item["jamais"]:
            (result["contradictions"] if strong else result["avertissements"]).append(
                f"{item['caracteristique']} prévu(e) « {value[0]} » ({value[1]}) : jamais observé(e) avec le besoin "
                f"{need} sur les {result['nombre']} {label} réelles qui le portent (valeurs observées : "
                f"{', '.join(f'{k} ×{n}' for k, n in list(item['avec'].items())[:6])})")
    result["recommandation"] = recommendation(real, planned, characteristics)
    return result


def recommendation(real: list, planned: dict, characteristics: list) -> list:
    """
    Cas réels à reprendre : combinaisons portant l'effet qui gardent les valeurs écrites par le test quand elles sont
    observées avec l'effet (ex. emballage), les plus fréquentes d'abord ; caractéristiques des règles et dimensions.
    """
    kept = real
    for label, _sql, _path in characteristics:
        value = planned.get(label)
        if value and value[1] == "valeur écrite dans le test":
            matching = [c for c in kept if c["valeurs"][label] == value[0]]
            kept = matching or kept
    merged = {}
    for combo in kept:
        key = tuple(sorted((k, v) for k, v in combo["valeurs"].items() if v != "∅"))
        merged[key] = merged.get(key, 0) + combo["nombre"]
    return [{"valeurs": dict(key), "nombre": n} for key, n in sorted(merged.items(), key=lambda kv: -kv[1])[:3]]


def analyse_feature(text: str, schema: str, query, since: str = "") -> list:
    """Analyse de chaque besoin attendu par la feature (règles et profils lus une fois)."""
    effects = expected_effects(text)
    if not effects:
        return []
    rules, known, values = active_rules(schema, query), profiles(schema, query), feature_values(text)
    out, seen = [], set()
    for effect in effects:
        key = (effect["entite"][0], effect["besoin"])
        if key in seen:
            continue
        seen.add(key)
        out.append({**analyse_effect(effect, schema, query, rules, known, values, since), "scenario": effect["scenario"],
                    "ligne": effect["ligne"]})
    return out


def report_lines(analysis: dict) -> list:
    """Bloc lisible (prompt, journal, commentaire du test) d'une analyse d'effet."""
    lines = [f"Effet attendu : {analysis['effet']} (scénario « {analysis.get('scenario', '')} », ligne "
             f"{analysis.get('ligne', '?')})"]
    if analysis.get("non_verifie"):
        return lines + [f"  non vérifié : {analysis['non_verifie']}"]
    lines.append(f"  Cas réels de la base de référence : {analysis['nombre']} {analysis['entite']} avec ce besoin.")
    for combo in analysis["cas_reels"][:4]:
        lines.append("    - " + ", ".join(f"{k} = {v}" for k, v in combo["valeurs"].items()) + f" (×{combo['nombre']})")
    for item in analysis["determinants"][:4]:
        lines.append(f"  Caractéristique déterminante {item['caracteristique']} : avec le besoin "
                     + ", ".join(f"{k} ×{n}" for k, n in list(item["avec"].items())[:6])
                     + " ; jamais avec : " + ", ".join(f"{k} ×{n}" for k, n in list(item["jamais"].items())[:6]))
    for rule in analysis["regles"]:
        for expr in rule["refus"]:
            lines.append(f"  Règle {rule['nom'] or rule['cle']} v{rule['version']} ({expr['origine']}) : « "
                         f"{expr['condition']} » ({expr['description']}) → pas de besoin {analysis['besoin']}")
        for expr in rule["decision"]["indecises"]:
            lines.append(f"  Condition complexe à vérifier (Claude), règle {rule['nom'] or rule['cle']} : « "
                         f"{expr['condition']} »")
    for item in analysis.get("recommandation") or []:
        lines.append("  Cas réel à reprendre : " + ", ".join(f"{k} = {v}" for k, v in item["valeurs"].items())
                     + f" (×{item['nombre']} dans la copie)")
    if analysis["prevu"]:
        lines.append("  Données prévues par le test : " + "; ".join(f"{k} = {v[0]} ({v[1]})"
                                                                   for k, v in analysis["prevu"].items()))
    for item in analysis["contradictions"]:
        lines.append(f"  ⛔ {item}")
    for item in analysis.get("avertissements") or []:
        lines.append(f"  ⚠ {item}")
    lines.append(f"  Requête : {analysis['requete']}")
    return lines


def check_files(worktree: str, files: list, schema: str, query, since: str = "") -> list:
    """Erreurs de lot (avant tout run) : besoins attendus contredits par les règles ou sans cas réel."""
    import os
    if not schema:
        return []
    errors = []
    for rel in files:
        path = os.path.join(worktree, *rel.split("/"))
        if not rel.endswith(".feature") or not os.path.exists(path):
            continue
        with open(path, encoding="utf-8", errors="replace") as f:
            text = f.read()
        for analysis in analyse_feature(text, schema, query, since):
            if analysis["contradictions"]:
                errors.append(f"{rel}:{analysis['ligne']} : {CATEGORY} — " + " | ".join(analysis["contradictions"])
                              + f". Règle générale : {LESSON}. Preuve :\n" + "\n".join(report_lines(analysis)))
    return errors


GUIDANCE = (
    "\nRÈGLES MÉTIER DU CLIENT (contrôle sans IA à chaque vérification de lot) :\n"
    f"- Leçon générale : {LESSON}.\n"
    "- Pour chaque effet attendu AVANT l'action testée (besoin d'une UP / OP / UE, statut…), les données du test "
    "reprennent les caractéristiques d'un cas réel de la base de référence qui présente cet effet (secteur, "
    "mécanisation, méthode de colisage, emballage…) ; cite en commentaire du test la règle du client et le cas réel "
    "(requête et résultat ci-dessous quand ils sont fournis).\n"
    "- Un test du Standard sert de modèle de steps et d'enchaînement, jamais de modèle de données : ses données sont "
    "réévaluées contre les règles du client.\n"
    "- Une règle du client qui contredit le ticket ou sa propre description est un constat (défaut produit probable), "
    "jamais contourné par le test.\n")


def prompt_block(worktree: str, files: list, schema: str, query, since: str = "") -> str:
    """Bloc des prompts : consignes, puis analyse des features existantes du lot (cas réels, règles, contradictions)."""
    import os
    lines = [GUIDANCE]
    for rel in files or []:
        path = os.path.join(worktree, *rel.split("/"))
        if not rel.endswith(".feature") or not os.path.exists(path) or not schema:
            continue
        with open(path, encoding="utf-8", errors="replace") as f:
            text = f.read()
        for analysis in analyse_feature(text, schema, query, since):
            lines.append(f"{rel} :")
            lines += report_lines(analysis)
    return "\n".join(lines) + "\n"
