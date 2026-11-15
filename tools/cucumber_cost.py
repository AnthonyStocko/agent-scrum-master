"""
Coût des lots Cucumber : plafond par lot et coût cumulé par spécifique (0 token).

- Plafond : CUCUMBER_LOT_BUDGET_USD (15 $ par défaut, 0 = sans plafond) sur le coût d'UNE exécution d'un lot
  (génération, corrections, analyse du dernier run). Au-delà : plus aucun appel ni run pour ce lot, arrêt avec le
  bilan. Chaque appel reçoit au plus le budget restant du lot.
- Coût cumulé par spécifique : .tmp/cucumber/<client>/couts.json, conservé d'une génération et d'une reprise à
  l'autre ; le coût d'un lot de plusieurs spécifiques est réparti à parts égales.
- Sessions reprises (--resume) : Claude Code renvoie dans total_cost_usd le coût CUMULÉ de la session et applique
  --max-budget-usd à ce cumul. Le cumul de chaque session est gardé (sessions.json) : le coût d'un appel est l'écart
  avec le cumul précédent, et le budget passé à Claude est le cumul précédent plus le budget restant du lot.
"""
import json
import os
from datetime import datetime

DEFAULT_LOT_BUDGET_USD = 15.0
FILE_NAME = "couts.json"


def lot_budget() -> float:
    """Plafond de coût d'une exécution de lot (USD) ; 0 = sans plafond."""
    try:
        return max(0.0, float(os.getenv("CUCUMBER_LOT_BUDGET_USD", "").strip() or DEFAULT_LOT_BUDGET_USD))
    except ValueError:
        return DEFAULT_LOT_BUDGET_USD


def start_lot(lot: dict) -> None:
    """Début d'une exécution du lot : coût déjà compté (exécutions précédentes) mis de côté."""
    lot["cout_debut"] = float((lot.get("tokens") or {}).get("cout_usd") or 0)
    lot.pop("plafond_cout", None)


def run_cost(lot: dict) -> float:
    """Coût de l'exécution en cours du lot (USD)."""
    return max(0.0, float((lot.get("tokens") or {}).get("cout_usd") or 0) - float(lot.get("cout_debut") or 0))


def remaining(lot: dict) -> float:
    """Budget restant de l'exécution en cours (USD) ; infini sans plafond."""
    budget = lot_budget()
    return float("inf") if not budget else max(0.0, budget - run_cost(lot))


def over_budget(lot: dict, label: str = "") -> bool:
    """Plafond atteint : marqué sur le lot (bilan) et annoncé une fois. Toujours False sans plafond."""
    if not lot_budget() or run_cost(lot) < lot_budget():
        return False
    if not lot.get("plafond_cout"):
        lot["plafond_cout"] = {"budget": lot_budget(), "cout": round(run_cost(lot), 4)}
        print(f"[LOT {label}] 💰 Plafond de coût du lot atteint ({run_cost(lot):.2f} $ ≥ {lot_budget():g} $, "
              f"CUCUMBER_LOT_BUDGET_USD) : plus aucun appel ni run, arrêt avec le bilan.")
    return True


def call_budget(lot: dict, per_call: str = "") -> str:
    """--max-budget-usd d'un appel : le plus petit du budget par appel (CUCUMBER_MAX_BUDGET_USD) et du restant."""
    left = remaining(lot) if lot is not None else float("inf")
    try:
        call = float(per_call) if str(per_call or "").strip() else float("inf")
    except ValueError:
        call = float("inf")
    value = min(left, call)
    return "" if value == float("inf") else f"{max(value, 0.01):.2f}"


def _path(client: str) -> str:
    from tools import cucumber_tools
    return os.path.join(cucumber_tools.cache_dir(client), FILE_NAME)


def load(client: str) -> dict:
    try:
        with open(_path(client), encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def record_lot_run(client: str, lot: dict, generation: str = "") -> dict:
    """Coût de l'exécution du lot ajouté au cumul de chacun de ses spécifiques (parts égales). Renvoie le cumul."""
    ids = [i for i in lot.get("ids") or [] if i] or ([lot["spec_correction"]] if lot.get("spec_correction") else [])
    cost = run_cost(lot)
    if not ids or cost <= 0:
        return load(client)
    data = load(client)
    share = cost / len(ids)
    for spec_id in ids:
        entry = data.setdefault(spec_id, {"total": 0.0, "executions": []})
        entry["total"] = round(float(entry.get("total") or 0) + share, 4)
        entry["executions"] = (entry.get("executions") or [])[-49:] + [{
            "date": datetime.now().isoformat(timespec="seconds"), "lot": lot.get("numero"),
            "generation": generation, "cout": round(share, 4),
            "plafond": bool(lot.get("plafond_cout"))}]
    os.makedirs(os.path.dirname(_path(client)), exist_ok=True)
    with open(_path(client), "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    return data


def spec_total(client: str, spec_id: str) -> float:
    return float((load(client).get(spec_id) or {}).get("total") or 0)


SESSIONS_FILE = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), ".tmp", "cucumber",
                             "sessions_cout.json")


def _sessions() -> dict:
    try:
        with open(SESSIONS_FILE, encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def session_base(session_id: str):
    """Cumul (USD) déjà dépensé par la session `session_id` ; None si inconnu (session antérieure au suivi)."""
    value = _sessions().get(session_id or "") if session_id else 0.0
    return None if value is None else float(value)


def record_session(session_id: str, cumulative) -> None:
    """Cumul de coût d'une session après un appel (total_cost_usd de Claude Code)."""
    try:
        cumulative = float(cumulative)
    except (TypeError, ValueError):
        return
    if not session_id:
        return
    data = _sessions()
    data[session_id] = round(cumulative, 6)
    os.makedirs(os.path.dirname(SESSIONS_FILE), exist_ok=True)
    with open(SESSIONS_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=1)


def resumed_call_budget(budget: str, base) -> str:
    """--max-budget-usd d'un appel qui reprend une session : cumul précédent + budget de l'appel. Cumul inconnu :
    pas de budget passé à Claude (un budget sous le cumul arrêterait l'appel d'office), le plafond du lot reste
    contrôlé par l'agent."""
    if not budget or not base:
        return "" if base is None else budget
    return f"{float(base) + float(budget):.2f}"


def call_cost(usage: dict, base) -> dict:
    """Usage d'un appel ramené à son propre coût : cout_usd = cumul de la session − cumul précédent."""
    if not usage or usage.get("cout_usd") in ("", None):
        return usage
    cumulative = float(usage["cout_usd"])
    record_session(usage.get("session_id", ""), cumulative)
    if base is None:
        usage["cout_cumul_inconnu"] = True
        return usage
    usage["cout_session"] = cumulative
    usage["cout_usd"] = round(max(0.0, cumulative - float(base)), 6)
    return usage
