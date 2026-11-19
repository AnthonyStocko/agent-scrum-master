"""
Préparation de l'environnement (étape 5 de l'onglet « Cucumber » simplifié, entre l'init et les tests), tous clients.

Chaque blocage de l'environnement (version de Java, Studio invalide, composant en dépendance manquante, service
externe, paramètres du job, accès) est cherché AVANT d'écrire un test, au lieu d'être découvert run après run.

Contrôles statiques (0 token, sans run) — ce module :
1. Versions : application / Standard / Cucumber du Standard / bibliothèque Cucumber de la même version (déduction de
   la version du client), version KIS connue, Java du projet de tests et de l'application comparé à celui du Standard
   à sa version (poms lus dans Bitbucket et dans le dépôt).
2. Studio à la version testée : tools/cucumber_anticipation (analyse statique d'anticipation, point 1.a).
3. Configuration copiée (voie A) : relevé des branchements vers des services externes (colonnes d'adresse, d'hôte,
   d'URL, de file, d'imprimante… des tables de configuration de la copie) — précolisage, WCS, TMS distant,
   messagerie, imprimantes, files, autres.
4. Job et profil : job renseigné, artefact monté (COMPONENT_OWNER / NAME) et version du composant du bon client,
   accès Jenkins, Bitbucket, Nexus, Groundcover.

Résultat : tableau [{module, maillon, etat, cause, action, preuve}] ; etat ∈ ok / bloque / attention / non_verifie.
Un blocage « fondamental » (versions, job) met l'étape en échec : aucun test ne peut passer. Les autres (Studio,
service externe) rendent seulement « non testables aujourd'hui » les fonctionnalités qui en dépendent.
"""
import hashlib
import json
import re

STATES = ("ok", "bloque", "attention", "non_verifie")
STATE_LABELS = {"ok": "OK", "bloque": "bloqué", "attention": "à vérifier", "non_verifie": "non vérifié"}
FUNDAMENTAL = ("versions", "job et profil")
MAX_EXTERNAL_COLUMNS = 400
MAX_VALUES_PER_COLUMN = 5
_POM_JAVA = re.compile(r"<(maven\.compiler\.release|maven\.compiler\.target|maven\.compiler\.source|java\.version|"
                       r"release)>\s*(?:1\.)?(\d+)\s*<")
# Services externes : colonnes candidates et valeurs qui ressemblent à une adresse
_ADDRESS_COLUMN = (r"(^|_)(url|uri|host|hostname|endpoint|server|ip|address_ip|queue|topic|broker|printer|smtp|wsdl|base_url|start_url)(_|$)")
_ADDRESS_VALUE = r"(://|^[a-z0-9][a-z0-9.-]*:[0-9]{2,5}(/|$)|^\\\\\\\\)"
_SKIPPED_TABLES = re.compile(r"(_histo|_history|_log|_log_line|_tbl|_dta|_status)$|^v_|^exc_message_histo|"
                             r"^kis_document|^kis_history|^kis_login|^kis_ai_|^tms_provider_msg", re.I)
CATEGORIES = (("précolisage", re.compile(r"cub|colis", re.I)), ("WCS", re.compile(r"wcs|mecan|sorter", re.I)),
              ("TMS distant", re.compile(r"tms|carrier|transport", re.I)),
              ("messagerie", re.compile(r"mail|smtp", re.I)),
              ("imprimante", re.compile(r"print|spool|report_exchange|label", re.I)),
              ("file de messages", re.compile(r"queue|topic|jms|amqp|kafka|broker|mq", re.I)))
_LOCAL_HOST = re.compile(r"://(localhost|127\.0\.0\.1|0\.0\.0\.0)(:|/|$)", re.I)


def row(module: str, maillon: str, etat: str, cause: str = "", action: str = "", preuve: str = "") -> dict:
    return {"module": module, "maillon": maillon, "etat": etat if etat in STATES else "non_verifie",
            "cause": cause, "action": action, "preuve": preuve}


# ---------------------------------------------------------------------------
# 1. Versions
# ---------------------------------------------------------------------------

def pom_java(text: str) -> int:
    """Version de Java ciblée par un pom (maven.compiler.release / target / source, java.version) ; 0 si absente."""
    found = {name: int(value) for name, value in _POM_JAVA.findall(text or "")}
    for name in ("maven.compiler.release", "release", "maven.compiler.target", "java.version",
                 "maven.compiler.source"):
        if found.get(name):
            return found[name]
    return 0


def _major(version: str) -> str:
    match = re.match(r"\D*(\d+(?:\.\d+)?)", str(version or ""))
    return match.group(1) if match else ""


def check_versions(deduction: dict, tests_java: int = 0, std_java: int = 0, app_java: int = 0,
                   std_java_source: str = "") -> list:
    """Versions de l'application, du Standard, du Cucumber du Standard, de la bibliothèque, KIS et Java."""
    module = "versions"
    deduction = deduction or {}
    rows = []
    client = deduction.get("version_client", "")
    standard = (deduction.get("standard") or {}).get("version", "")
    if not client:
        return [row(module, "version du client", "bloque", "version du client non choisie",
                    "choisir la version du client (étape 1, « Données du projet »)")]
    rows.append(row(module, "application → Standard", "ok" if standard else "bloque",
                    "" if standard else "version du Standard non déduite de la version du client",
                    "" if standard else "vérifier le pom de l'application à cette version",
                    f"application {client} → Standard {standard or '?'} "
                    f"({(deduction.get('standard') or {}).get('detail', '')})"))
    cucumber_std = (deduction.get("cucumber_standard") or {}).get("version", "")
    same = bool(cucumber_std) and _major(cucumber_std) == _major(standard)
    rows.append(row(module, "Cucumber du Standard", "ok" if same else "bloque",
                    "" if same else f"Cucumber du Standard {cucumber_std or 'introuvable'} ≠ Standard {standard}",
                    "" if same else "créer ou choisir la référence du Cucumber du Standard à la version du Standard",
                    f"référence {(deduction.get('cucumber_standard') or {}).get('ref', '?')}"))
    library = deduction.get("bibliotheque") or {}
    aligned = bool(library.get("cible")) and library.get("cible") == library.get("actuelle")
    rows.append(row(module, "bibliothèque Cucumber", "ok" if aligned else "bloque",
                    "" if aligned else f"bibliothèque {library.get('actuelle') or '?'} au lieu de "
                                       f"{library.get('cible') or '?'}",
                    "" if aligned else "aligner la bibliothèque du projet de tests (étape 2)",
                    library.get("source", "")))
    kis = (deduction.get("kis") or {}).get("kis", "")
    rows.append(row(module, "socle KIS (KIS_VERSION)", "ok" if kis else "bloque",
                    "" if kis else "version KIS inconnue pour cette version du client",
                    "" if kis else "renseigner la version KIS (build vert du composant ou réponse à la question)",
                    f"KIS {kis or '?'} ({(deduction.get('kis') or {}).get('source', '')})"))
    for label, java in (("Java du projet de tests", tests_java), ("Java de l'application", app_java)):
        if not java:
            continue
        if not std_java:
            rows.append(row(module, label, "non_verifie", f"Java {java} ; Java du Standard {standard} illisible", "",
                            ""))
        elif java > std_java:
            rows.append(row(module, label, "bloque",
                            f"compilé en Java {java}, le Standard {standard} (socle KIS {kis or '?'}) est en Java "
                            f"{std_java} : classes refusées à l'exécution (UnsupportedClassVersionError)",
                            f"compiler en Java {std_java} ou monter le socle", f"{std_java_source}"))
        else:
            rows.append(row(module, label, "ok", "", "", f"Java {java} ≤ Java {std_java} du Standard {standard}"))
    return rows


def std_java_version(std, candidates, repos=("wms-preparation-backend", "wms-content-backend")) -> tuple:
    """(Java, preuve) du Standard à sa version : pom.xml racine d'un dépôt backend ; (0, '') si illisible."""
    for repo in repos:
        try:
            ref = std.ref(repo, candidates)
            java = pom_java(std.show(repo, ref, "pom.xml")) if ref else 0
        except Exception:  # noqa: BLE001 - Bitbucket injoignable : non vérifié
            java = 0
        if java:
            return java, f"{repo}/pom.xml @ {std.ref_name(repo, ref)}"
    return 0, ""


# ---------------------------------------------------------------------------
# 3. Services externes de la configuration copiée
# ---------------------------------------------------------------------------

def category(*texts) -> str:
    joined = " ".join(str(t or "") for t in texts)
    return next((label for label, pattern in CATEGORIES if pattern.search(joined)), "autre")


def external_connections(schema: str, query, batch: int = 40) -> list:
    """
    Branchements externes de la copie : [{categorie, table, colonne, code, valeur}]. `query(sql, schema)` → [{"code"}].
    Code de la ligne lu quand la table a une colonne « code » (ex. CUBING de wms_preparation_saas_param). Valeurs
    vers localhost exclues ; tables d'historique et vues ignorées. Requêtes groupées (UNION ALL) par `batch` colonnes.
    """
    columns, coded = [], set()
    try:
        for r in query("SELECT c.table_name || '@@' || c.column_name AS code FROM information_schema.columns c "
                       "JOIN information_schema.tables t ON t.table_schema = c.table_schema AND t.table_name = "
                       "c.table_name AND t.table_type = 'BASE TABLE' WHERE c.table_schema = current_schema() AND "
                       "((c.data_type IN ('text', 'character varying') AND c.column_name ~* "
                       f"'{_ADDRESS_COLUMN}') OR c.column_name = 'code') ORDER BY 1", schema):
            table, _, column = str(r.get("code", "")).partition("@@")
            if not table or not column or _SKIPPED_TABLES.search(table):
                continue
            if column == "code":
                coded.add(table)
            else:
                columns.append((table, column))
    except Exception:  # noqa: BLE001
        return []
    columns = columns[:MAX_EXTERNAL_COLUMNS]
    found = []
    for index in range(0, len(columns), batch):
        parts = []
        for table, column in columns[index:index + batch]:
            code = "coalesce(\"code\"::text, '')" if table in coded else "''"
            parts.append(f"(SELECT '{table}@@{column}@@' || {code} || '@@' || \"{column}\" AS code FROM \"{table}\" "
                         f"WHERE \"{column}\" ~* '{_ADDRESS_VALUE}' LIMIT {MAX_VALUES_PER_COLUMN})")
        try:
            values = query(" UNION ALL ".join(parts), schema)
        except Exception:  # noqa: BLE001 - une colonne illisible : colonne par colonne
            values = []
            for part in parts:
                try:
                    values += query(part[1:-1], schema)
                except Exception:  # noqa: BLE001
                    continue
        for item in values:
            table, column, code, value = (str(item.get("code", "")).split("@@", 3) + ["", "", "", ""])[:4]
            value = value.strip()
            if value and value != "NULL" and not _LOCAL_HOST.search(value):
                found.append({"categorie": category(table, column, code, value), "table": table, "colonne": column,
                              "code": code, "valeur": value})
    return found


def check_external(connections: list, copied: bool) -> list:
    module = "services externes"
    if not copied:
        return [row(module, "branchements de la copie", "non_verifie", "configuration copiée inactive",
                    "", "relevé fait seulement en voie A")]
    if not connections:
        return [row(module, "branchements de la copie", "ok", "", "", "aucune adresse externe dans la copie")]
    return [row(module, f"{c['categorie']} : {c['valeur']}" + (f" ({c['code']})" if c.get("code") else ""), "attention",
                "service externe de la configuration copiée : absent de l'environnement de test s'il n'y est pas "
                "monté", "joignabilité contrôlée par le run témoin ; s'il est injoignable, le neutraliser dans "
                         "l'init de la configuration copiée", f"{c['table']}.{c['colonne']}")
            for c in connections]


# ---------------------------------------------------------------------------
# 4. Job, profil et accès
# ---------------------------------------------------------------------------

def check_job(job: str, expected: dict, current: dict, roles: dict, access: dict) -> list:
    """
    `expected` / `current` : {composant_proprietaire, composant_nom, composant_version} attendus (application du
    client) et valeurs du job ; `roles` : rôle → nom du paramètre ; `access` : {service: '' | erreur}.
    """
    module = "job et profil"
    rows = [row(module, "job Jenkins", "ok" if job else "bloque", "" if job else "job du client non renseigné",
                "" if job else "renseigner le job Jenkins du profil client", job)]
    for role, label in (("composant_proprietaire", "COMPONENT_OWNER"), ("composant_nom", "COMPONENT_NAME"),
                        ("composant_version", "COMPONENT_VERSION")):
        param = (roles or {}).get(role) or label
        want, have = str((expected or {}).get(role) or ""), str((current or {}).get(role) or "")
        if not want:
            rows.append(row(module, param, "bloque", f"{label} attendu inconnu", "choisir la version du client"))
        elif have and have != want:
            rows.append(row(module, param, "attention",
                            f"valeur par défaut du job ({have}) ≠ client ({want}) : imposée par l'agent à chaque run",
                            "", f"attendu {want}"))
        else:
            rows.append(row(module, param, "ok", "", "", f"{want}"))
    for service, error in (access or {}).items():
        rows.append(row(module, f"accès {service}", "bloque" if error else "ok", error or "",
                        f"vérifier la configuration {service} (onglet Configuration)" if error else ""))
    return rows


# ---------------------------------------------------------------------------
# Résultat
# ---------------------------------------------------------------------------

def fingerprint(deduction: dict, reference: dict = None) -> str:
    """Empreinte de ce qui rend les contrôles caducs : version du client et du Standard, KIS, copie de référence."""
    deduction = deduction or {}
    data = {"client": deduction.get("version_client", ""),
            "standard": (deduction.get("standard") or {}).get("version", ""),
            "kis": (deduction.get("kis") or {}).get("kis", ""),
            "bibliotheque": (deduction.get("bibliotheque") or {}).get("actuelle", ""),
            "reference": {k: (reference or {}).get(k) for k in ("date", "versions_source", "source")}}
    return hashlib.sha256(json.dumps(data, sort_keys=True, ensure_ascii=False).encode("utf-8")).hexdigest()[:16]


def summary(rows: list) -> dict:
    counts = {state: sum(1 for r in rows if r["etat"] == state) for state in STATES}
    fundamental = [r for r in rows if r["etat"] == "bloque" and r["module"] in FUNDAMENTAL]
    blocked = [r for r in rows if r["etat"] == "bloque"]
    status = "echec" if fundamental else "ok"
    text = (f"{counts['ok']} OK, {counts['bloque']} bloqué(s), {counts['attention']} à vérifier, "
            f"{counts['non_verifie']} non vérifié(s)")
    if fundamental:
        text += " — blocage de l'environnement : " + " ; ".join(f"{r['maillon']} ({r['cause']})"
                                                                 for r in fundamental[:3])
    elif blocked:
        text += " — fonctionnalités dépendantes non testables : " + ", ".join(r["maillon"] for r in blocked[:4])
    return {"etat": status, "resultat": text + ".", "compteurs": counts}


def table_markdown(rows: list) -> str:
    lines = ["| Module | Maillon | État | Cause | Action |", "|---|---|---|---|---|"]
    for r in rows:
        cells = [r["module"], r["maillon"], STATE_LABELS.get(r["etat"], r["etat"]), r["cause"], r["action"]]
        lines.append("| " + " | ".join(str(c).replace("|", "/").replace("\n", " ") for c in cells) + " |")
    return "\n".join(lines)


def blocked_dependencies(rows: list) -> list:
    """Maillons bloqués (hors blocage fondamental) : base du classement « non testables aujourd'hui »."""
    return [{"module": r["module"], "maillon": r["maillon"], "cause": r["cause"]}
            for r in rows if r["etat"] == "bloque" and r["module"] not in FUNDAMENTAL]
