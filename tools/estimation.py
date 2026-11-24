"""
Chiffrage de l'analyse d'un ticket (onglet Lancement), pour tous les projets.

- Section « Chiffrage » à la fin de l'analyse, après la solution : tableau des activités (développement Java par
  module, personnalisation Studio, paramétrage et données, tests, revue et recette, livraison) avec une fourchette
  basse / probable / haute dans l'unité du projet, et les éléments qui la justifient ; risques et hypothèses ;
  niveau de confiance et sa raison ; calibrage sur l'historique.
- Total recalculé sans IA (somme des activités) après chaque écriture de l'analyse ; section contrôlée.
- Calibrage : tickets similaires de la mémoire (chiffrage et temps réellement passé) et de Jira (temps passé,
  estimation initiale, points), injectés dans le prompt ; sans historique exploitable, le prompt le dit et demande
  une fourchette élargie.
- Publication : SEULEMENT à la validation de l'analyse (analysis_publication.validate) — valeur probable du total
  écrite dans le champ d'estimation du projet (estimation initiale ou points d'histoire).
- Après livraison : temps réellement passé (Jira) comparé au chiffrage, enregistré dans la mémoire du ticket.
- Épopées : chiffrage de chaque tâche enfant et total.
Réglages par projet (fichier ESTIMATION_SETTINGS_FILE, onglet Configuration) : unité, champ Jira, activités.
"""
import json
import os
import re
from datetime import datetime

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SETTINGS_FILE = os.path.join(ROOT, ".tmp", "estimation_projets.json")
SECTION = "Chiffrage"
ACTIVITIES = (("dev_java", "Développement Java"), ("studio", "Personnalisation Studio"),
              ("parametrage", "Paramétrage et données"), ("tests", "Tests (unitaires, Cucumber)"),
              ("recette", "Revue et recette"), ("livraison", "Livraison"))
UNITS = {"jours-homme": "j/h", "points": "pts"}
DEFAULT_FIELD = "timeoriginalestimate"
CONFIDENCE = ("élevé", "moyen", "faible")
MAX_HISTORY = 5
_HEADING = re.compile(r"^(#{1,4})\s+(.*)$")
_NUMBER = re.compile(r"-?\d+(?:[.,]\d+)?")


# ---------------------------------------------------------------------------
# Réglages par projet
# ---------------------------------------------------------------------------

def _settings_path() -> str:
    return os.getenv("ESTIMATION_SETTINGS_FILE", "").strip() or SETTINGS_FILE


def _all_settings() -> dict:
    try:
        with open(_settings_path(), encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def settings(project: str) -> dict:
    """{unite, champ, activites (clés), heures_par_jour} du projet ; défauts : variables ESTIMATION_*."""
    stored = _all_settings().get((project or "").strip().upper()) or {}
    unit = stored.get("unite") or os.getenv("ESTIMATION_UNIT", "jours-homme")
    keys = [k for k, _label in ACTIVITIES]
    try:
        hours = float(stored.get("heures_par_jour") or os.getenv("ESTIMATION_HOURS_PER_DAY", "8"))
    except ValueError:
        hours = 8.0
    return {"unite": unit if unit in UNITS else "jours-homme",
            "champ": (stored.get("champ") or os.getenv("ESTIMATION_FIELD", DEFAULT_FIELD)).strip(),
            "activites": [k for k in (stored.get("activites") or keys) if k in keys] or keys,
            "heures_par_jour": hours}


def save_settings(project: str, values: dict) -> dict:
    data = _all_settings()
    data[(project or "").strip().upper()] = {k: values[k] for k in ("unite", "champ", "activites", "heures_par_jour")
                                             if k in values}
    os.makedirs(os.path.dirname(_settings_path()), exist_ok=True)
    with open(_settings_path(), "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    return settings(project)


def activity_labels(config: dict) -> list:
    return [label for key, label in ACTIVITIES if key in config["activites"]]


# ---------------------------------------------------------------------------
# Lecture et cohérence de la section
# ---------------------------------------------------------------------------

def _plain(text: str) -> str:
    import unicodedata
    text = unicodedata.normalize("NFKD", text or "")
    return "".join(c for c in text if not unicodedata.combining(c)).lower()


def section_bounds(text: str) -> tuple:
    """(début, fin) en lignes de la section « Chiffrage » ; (-1, -1) si absente."""
    lines = (text or "").splitlines()
    for index, line in enumerate(lines):
        match = _HEADING.match(line.strip())
        if match and "chiffrage" in _plain(match.group(2)):
            level = len(match.group(1))
            end = next((j for j in range(index + 1, len(lines))
                        if (_HEADING.match(lines[j].strip()) and len(_HEADING.match(lines[j].strip()).group(1)) <= level)
                        or lines[j].strip() == "---"), len(lines))
            return index, end
    return -1, -1


def _cells(line: str) -> list:
    return [c.strip() for c in line.strip().strip("|").split("|")]


def _number(cell: str):
    match = _NUMBER.search((cell or "").replace(" ", "").replace(" ", ""))
    return float(match.group(0).replace(",", ".")) if match else None


def parse(text: str) -> dict:
    """
    {present, unite, lignes: [{activite, basse, probable, haute, justification, index}], total: {…, index},
    confiance, calibrage, risques} de la section « Chiffrage » (premier tableau à colonnes Basse / Probable / Haute).
    """
    start, end = section_bounds(text)
    result = {"present": start >= 0, "unite": "", "lignes": [], "total": None, "confiance": "", "calibrage": False,
              "risques": False}
    if start < 0:
        return result
    lines = text.splitlines()[start:end]
    body = _plain("\n".join(lines))
    result["unite"] = next((u for u in UNITS if u in body or UNITS[u] in body), "")
    result["confiance"] = next((c for c in CONFIDENCE if re.search(rf"confiance[^\n]*\b{_plain(c)}\b", body)), "")
    result["calibrage"] = "calibrage" in body or "historique" in body
    result["risques"] = "risque" in body
    header = None
    for offset, line in enumerate(lines):
        if not line.strip().startswith("|"):
            if header is not None and result["lignes"]:
                break
            continue
        cells = _cells(line)
        if header is None:
            plain = [_plain(c) for c in cells]
            if any("basse" in c for c in plain) and any("probable" in c for c in plain):
                header = {"basse": next(i for i, c in enumerate(plain) if "basse" in c),
                          "probable": next(i for i, c in enumerate(plain) if "probable" in c),
                          "haute": next((i for i, c in enumerate(plain) if "haute" in c), None),
                          "justification": next((i for i, c in enumerate(plain) if "justif" in c or "element" in c),
                                                None)}
            continue
        if set("".join(cells)) <= set("-: "):
            continue
        row = {"activite": cells[0].strip("* "), "index": start + offset,
               "basse": _number(cells[header["basse"]]) if header["basse"] < len(cells) else None,
               "probable": _number(cells[header["probable"]]) if header["probable"] < len(cells) else None,
               "haute": _number(cells[header["haute"]]) if header["haute"] is not None
               and header["haute"] < len(cells) else None,
               "justification": cells[header["justification"]] if header["justification"] is not None
               and header["justification"] < len(cells) else ""}
        if _plain(row["activite"]).startswith("total"):
            result["total"] = row
        else:
            result["lignes"].append(row)
    return result


def totals(parsed: dict) -> dict:
    """Somme des activités : {basse, probable, haute}."""
    return {k: round(sum(r[k] or 0 for r in parsed["lignes"]), 2) for k in ("basse", "probable", "haute")}


def _fmt(value: float) -> str:
    return (f"{value:.2f}".rstrip("0").rstrip(".") if value is not None else "?").replace(".", ",")


def normalize(text: str, unit: str = "") -> tuple:
    """
    (texte, corrigé ?) : ligne « Total » du tableau recalculée (somme des activités), ajoutée si absente ; unité du
    projet écrite sous le titre si la section ne la donne pas. Aucune IA : le total publié est toujours la somme des
    activités.
    """
    parsed = parse(text)
    if not parsed["lignes"]:
        return text, False
    if unit and not parsed["unite"]:
        start, _end = section_bounds(text)
        lines = text.splitlines()
        lines[start + 1:start + 1] = ["", f"Unité : {unit}."]
        text = "\n".join(lines) + ("\n" if text.endswith("\n") else "")
        fixed, _changed = normalize(text)
        return fixed, True
    expected = totals(parsed)
    total = parsed["total"]
    if total and all(abs((total[k] or 0) - expected[k]) < 0.01 for k in expected):
        return text, False
    lines = text.splitlines()
    width = len(_cells(lines[parsed["lignes"][0]["index"]]))
    header_index = parsed["lignes"][0]["index"] - 2
    header = [_plain(c) for c in _cells(lines[header_index])] if header_index >= 0 else []
    cells = [""] * width
    cells[0] = "**Total**"
    for key in ("basse", "probable", "haute"):
        column = next((i for i, c in enumerate(header) if key in c), None)
        if column is not None and column < width:
            cells[column] = f"**{_fmt(expected[key])}**"
    row = "| " + " | ".join(cells) + " |"
    if total:
        lines[total["index"]] = row
    else:
        lines.insert(parsed["lignes"][-1]["index"] + 1, row)
    return "\n".join(lines) + ("\n" if text.endswith("\n") else ""), True


def check(text: str, config: dict) -> list:
    """Problèmes de la section (vide si cohérente) : présence, fourchettes, total, unité, confiance, calibrage."""
    parsed = parse(text)
    if not parsed["present"]:
        return ["section « Chiffrage » absente"]
    problems = []
    if not parsed["lignes"]:
        problems.append("tableau des activités (Basse / Probable / Haute) absent")
    for row in parsed["lignes"]:
        values = [row["basse"], row["probable"], row["haute"]]
        if None in values:
            problems.append(f"« {row['activite']} » : fourchette incomplète")
        elif not values[0] <= values[1] <= values[2]:
            problems.append(f"« {row['activite']} » : basse ≤ probable ≤ haute non respecté")
        if not row["justification"]:
            problems.append(f"« {row['activite']} » : aucun élément justificatif")
    expected = totals(parsed)
    if parsed["lignes"] and (not parsed["total"] or any(abs((parsed["total"][k] or 0) - expected[k]) >= 0.01
                                                         for k in expected)):
        problems.append("total différent de la somme des activités")
    if parsed["unite"] != config["unite"]:
        problems.append(f"unité attendue : {config['unite']}")
    if not parsed["confiance"]:
        problems.append("niveau de confiance (élevé / moyen / faible) absent")
    if not parsed["calibrage"]:
        problems.append("calibrage sur l'historique absent")
    return problems


# ---------------------------------------------------------------------------
# Historique (calibrage)
# ---------------------------------------------------------------------------

def _hours_to_unit(hours: float, config: dict) -> float:
    return round(hours / config["heures_par_jour"], 2)


def memory_history(project: str, query_text: str, exclude: str, components=None) -> list:
    """Tickets similaires de la mémoire avec chiffrage et/ou temps réel : [{cle, titre, chiffrage, reel, unite}]."""
    from agent_socle import memory_tools
    out = []
    for item in memory_tools.find_similar_tickets(project, component_name=components, query_text=query_text,
                                                  exclude_issue_key=exclude, max_results=MAX_HISTORY * 2):
        mem = item["memory"]
        estimate, actual = mem.get("chiffrage") or {}, mem.get("reel") or {}
        if estimate or actual:
            out.append({"cle": mem["issue_key"], "titre": mem.get("summary", ""), "source": "mémoire",
                        "chiffrage": estimate.get("probable"), "unite": estimate.get("unite", ""),
                        "reel": actual.get("valeur"), "raisons": ", ".join(item["reasons"])})
    return out[:MAX_HISTORY]


def jira_history(project: str, query_text: str, exclude: str, config: dict, search=None) -> list:
    """
    Tickets terminés du projet, proches par le texte, avec temps passé ou estimation : [{cle, titre, reel,
    estimation, points}] (reel et estimation dans l'unité du projet). `search(jql, fields)` → issues ; Jira
    injoignable : [].
    """
    words = [w for w in re.findall(r"[A-Za-zÀ-ÿ]{5,}", query_text or "")][:6]
    if not words or not project:
        return []
    terms = " OR ".join(f'text ~ "{w}"' for w in dict.fromkeys(words))
    field = config["champ"]
    jql = (f"project = {project} AND statusCategory = Done AND key != {exclude or 'X-0'} AND ({terms}) "
           f"AND (timespent > 0 OR timeoriginalestimate > 0) ORDER BY resolved DESC")
    fields = ["summary", "timespent", "timeoriginalestimate"] + ([field] if field.startswith("customfield_") else [])
    try:
        issues = (search or _jira_search)(jql, fields)
    except Exception:  # noqa: BLE001 - Jira injoignable : pas d'historique Jira
        return []
    out = []
    for issue in issues[:MAX_HISTORY]:
        f = issue.get("fields") or {}
        out.append({"cle": issue.get("key", ""), "titre": f.get("summary", ""), "source": "Jira",
                    "reel": _hours_to_unit((f.get("timespent") or 0) / 3600, config) if f.get("timespent") else None,
                    "estimation": _hours_to_unit((f.get("timeoriginalestimate") or 0) / 3600, config)
                    if f.get("timeoriginalestimate") else None,
                    "points": f.get(field) if field.startswith("customfield_") else None})
    return out


def _jira_search(jql: str, fields: list) -> list:
    import requests
    from agent_socle.jira_tools import _jira_connection
    api, auth = _jira_connection()
    response = requests.post(f"{api}/search/jql", auth=auth, json={"jql": jql, "maxResults": MAX_HISTORY,
                                                                   "fields": fields}, timeout=30)
    if response.status_code != 200:
        raise RuntimeError(f"Jira {response.status_code}")
    return response.json().get("issues", [])


def history_block(history: list, config: dict) -> str:
    unit = config["unite"]
    if not history:
        return ("Historique : AUCUN ticket similaire exploitable (ni mémoire de l'agent, ni Jira avec temps passé ou "
                "estimation). Dis-le dans le calibrage et ÉLARGIS la fourchette (haute nettement au-dessus de la "
                "probable) ; confiance au plus « moyen ».")
    lines = [f"Historique des tickets similaires (unité {unit}) — compare ton chiffrage et dis s'il est au-dessus ou "
             f"en dessous de ces tickets, et pourquoi :"]
    for item in history:
        parts = [f"{item['source']} {item['cle']} « {item['titre'][:80]} »"]
        if item.get("chiffrage") is not None:
            parts.append(f"chiffré {item['chiffrage']} {item.get('unite') or unit}")
        if item.get("estimation") is not None:
            parts.append(f"estimation initiale {item['estimation']} {unit}")
        if item.get("points") is not None:
            parts.append(f"{item['points']} points")
        if item.get("reel") is not None:
            parts.append(f"réellement passé {item['reel']} {unit}")
        lines.append("- " + ", ".join(parts))
    return "\n".join(lines)


def prompt_block(project: str, history: list, epic: bool = False) -> str:
    """Consignes de la section « Chiffrage » (ajoutées au prompt d'analyse, de révision et d'épopée)."""
    config = settings(project)
    unit = config["unite"]
    labels = activity_labels(config)
    where = ("à la fin de CHAQUE fichier d'analyse de tâche" if epic
             else "à la fin de l'analyse, après la solution proposée (Partie 2)")
    return f"""
=== CHIFFRAGE (obligatoire) ===
Ajoute {where} une section « ## 💰 Chiffrage », dans l'unité du projet : {unit}.
1. Tableau EXACTEMENT de cette forme (une ligne par activité concernée ; « Développement Java » : une ligne par
   module, ex. « Développement Java — wms-preparation-core ») :
| Activité | Basse | Probable | Haute | Éléments justificatifs |
|---|---|---|---|---|
| Développement Java — <module> | 1 | 1,5 | 2,5 | classes et fichiers à créer ou modifier |
| **Total** | **…** | **…** | **…** | |
   Activités du projet : {", ".join(labels)}. Valeurs en {unit}, basse ≤ probable ≤ haute ; la ligne Total est la
   somme des activités (recalculée par l'agent). Éléments justificatifs : fichiers et classes à créer ou modifier,
   processus Studio touchés, règles et paramétrages, scénarios de test, dépendances.
2. « **Risques et hypothèses** » : ce qui élargit la fourchette (zone de code mal connue, spécification floue,
   dépendance externe, écart de version) et les hypothèses retenues.
3. « **Confiance** : élevé | moyen | faible » avec la raison.
4. « **Calibrage sur l'historique** » : comparaison avec les tickets ci-dessous (au-dessus / en dessous, pourquoi).
{history_block(history, config)}
=== FIN CHIFFRAGE ===
"""


# ---------------------------------------------------------------------------
# Publication (à la validation seulement) et retour d'expérience
# ---------------------------------------------------------------------------

def jira_update(parsed: dict, config: dict) -> dict:
    """Champs Jira à écrire : valeur probable du total dans le champ du projet ; {} sans total."""
    probable = totals(parsed)["probable"] if parsed["lignes"] else None
    if not probable:
        return {}
    field = config["champ"]
    if field in ("timeoriginalestimate", "timetracking", "originalEstimate"):
        hours = probable * config["heures_par_jour"] if config["unite"] == "jours-homme" else probable
        return {"timetracking": {"originalEstimate": f"{_fmt(hours).replace(',', '.')}h"}}
    return {field: probable}


def publish(issue_key: str, text: str, project: str = "", put=None) -> dict:
    """
    Écrit la valeur probable dans le champ d'estimation du projet — appelée UNIQUEMENT à la validation de l'analyse.
    Enregistre le chiffrage validé dans la mémoire du ticket. Renvoie {ecrit, champs, message}.
    """
    if os.getenv("ESTIMATION_ENABLED", "1").strip() == "0":
        return {"ecrit": False, "champs": {}, "message": "chiffrage désactivé (ESTIMATION_ENABLED=0)"}
    project = project or issue_key.split("-")[0]
    config = settings(project)
    parsed = parse(text)
    fields = jira_update(parsed, config)
    if not fields:
        return {"ecrit": False, "champs": {}, "message": "aucun chiffrage dans l'analyse"}
    (put or _jira_put_fields)(issue_key, fields)
    record_estimate(issue_key, project, parsed, config)
    value = totals(parsed)["probable"]
    return {"ecrit": True, "champs": fields, "message": f"chiffrage {_fmt(value)} {config['unite']} écrit dans "
                                                        f"{next(iter(fields))}"}


def _jira_put_fields(issue_key: str, fields: dict) -> None:
    import requests
    from agent_socle.jira_tools import _jira_connection
    api, auth = _jira_connection()
    response = requests.put(f"{api}/issue/{issue_key}", auth=auth, json={"fields": fields}, timeout=30)
    if response.status_code not in (200, 204):
        raise RuntimeError(f"Jira a refusé le chiffrage ({response.status_code}) : {response.text[:300]}")


def record_estimate(issue_key: str, project: str, parsed: dict, config: dict) -> None:
    from agent_socle import memory_tools
    data = memory_tools.load_ticket_memory(issue_key, project)
    data["chiffrage"] = {**totals(parsed), "unite": config["unite"], "date": datetime.now().isoformat(timespec="seconds"),
                         "activites": [{k: r[k] for k in ("activite", "basse", "probable", "haute")}
                                       for r in parsed["lignes"]]}
    memory_tools._write_memory(data)


def record_actuals(project: str, fetch=None) -> list:
    """
    Tickets livrés (statut terminé) dont le chiffrage est mémorisé sans temps réel : temps passé (Jira) comparé au
    chiffrage, enregistré dans la mémoire. `fetch(key)` → {done, timespent (s)}. Renvoie les écarts enregistrés.
    """
    from agent_socle import memory_tools
    config = settings(project)
    out = []
    for mem in memory_tools._load_all():
        if (project and mem.get("project_key") != project.upper()) or not mem.get("chiffrage") or mem.get("reel"):
            continue
        try:
            info = (fetch or _jira_actuals)(mem["issue_key"])
        except Exception:  # noqa: BLE001 - Jira injoignable : réessayé à la prochaine analyse
            continue
        if not info.get("done") or not info.get("timespent"):
            continue
        actual = _hours_to_unit(info["timespent"] / 3600, config) if config["unite"] == "jours-homme" \
            else info.get("points")
        estimate = mem["chiffrage"].get("probable")
        data = memory_tools.load_ticket_memory(mem["issue_key"], mem.get("project_key", ""))
        data["reel"] = {"valeur": actual, "unite": config["unite"], "date": datetime.now().isoformat(timespec="seconds"),
                        "ecart": round(actual - estimate, 2) if actual is not None and estimate is not None else None}
        memory_tools._write_memory(data)
        out.append({"cle": mem["issue_key"], **data["reel"], "chiffrage": estimate})
    return out


def _jira_actuals(issue_key: str) -> dict:
    import requests
    from agent_socle.jira_tools import _jira_connection
    api, auth = _jira_connection()
    response = requests.get(f"{api}/issue/{issue_key}?fields=timespent,status", auth=auth, timeout=15)
    if response.status_code != 200:
        raise RuntimeError(f"Jira {response.status_code}")
    fields = response.json().get("fields") or {}
    category = ((fields.get("status") or {}).get("statusCategory") or {}).get("key", "")
    return {"done": category == "done", "timespent": fields.get("timespent") or 0}


# ---------------------------------------------------------------------------
# Épopées
# ---------------------------------------------------------------------------

def epic_summary(task_texts: list, project: str) -> str:
    """
    Chiffrage de l'épopée : une ligne par tâche (total de son analyse) et total général ; `task_texts` :
    [(titre, texte de l'analyse)].
    """
    config = settings(project)
    rows, grand = [], {"basse": 0.0, "probable": 0.0, "haute": 0.0}
    for title, text in task_texts:
        if not (text or "").strip():
            rows.append(f"| {title} | analyse manquante | analyse manquante | analyse manquante |")
            continue
        parsed = parse(text)
        if not parsed["lignes"]:
            rows.append(f"| {title} | chiffrage absent | chiffrage absent | chiffrage absent |")
            continue
        sums = totals(parsed)
        for key in grand:
            grand[key] += sums[key]
        rows.append(f"| {title} | {_fmt(sums['basse'])} | {_fmt(sums['probable'])} | {_fmt(sums['haute'])} |")
    return "\n".join([f"## 💰 Chiffrage de l'épopée ({config['unite']})", "",
                      "| Tâche | Basse | Probable | Haute |", "|---|---|---|---|", *rows,
                      f"| **Total** | **{_fmt(grand['basse'])}** | **{_fmt(grand['probable'])}** | "
                      f"**{_fmt(grand['haute'])}** |"])
