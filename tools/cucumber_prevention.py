"""
Prévention avant écriture (0 token) : les contrôles automatiques qui refusent le plus souvent les fichiers d'un
client sont donnés à l'agent AVANT qu'il écrive, au lieu d'être découverts après et corrigés par un appel séparé.

1. Relevé (record) : les erreurs du PREMIER contrôle d'un lot (ce que l'agent a écrit, avant toute correction) sont
   comptées par type d'erreur (message sans chemin, ligne ni valeurs : même contrôle, même règle), avec les
   derniers exemples et les steps en cause. Fichier : .tmp/cucumber/<client>/controles.json.
2. Consignes (block) : les types relevés au moins MIN_COUNT fois, les plus fréquents d'abord, avec un exemple ;
   pour les colonnes de DataTable refusées, les colonnes que le Standard accepte pour chaque step en cause
   (reference["colonnes_par_step"]). Injecté dans le prompt du lot (${prevention}).
3. Contrôle par l'agent pendant qu'il écrit : tools/cucumber_compile.py vérifie aussi les colonnes de DataTable
   des features modifiées (colonnes_std.json écrit par main.py), comme le contrôle après lot.

CUCUMBER_PREVENTION=0 : ni relevé, ni consignes, ni contrôle des colonnes par l'agent.
"""
import json
import os
import re
from datetime import datetime

from tools import cucumber_tools

FORMAT = 1
FILE_NAME = "controles.json"
COLUMNS_FILE = "colonnes_std.json"
MIN_COUNT = 2              # occurrences avant de devenir une consigne
MAX_RULES = 8              # consignes injectées au plus
MAX_EXAMPLES = 3
MAX_KINDS = 200            # types d'erreur gardés au plus
MAX_COLUMNS = 25           # colonnes du Standard citées par step
MAX_CHARS = 3500
_PATH = re.compile(r"^(?:[\w.$-]+/)+[\w.$-]+(?::\d+)?\s*:?\s*")
_QUOTED = re.compile(r"«[^»]*»|\"[^\"]*\"|'[^']*'")
_STEP = re.compile(r"pour le step « ([^»]+) »")


def enabled() -> bool:
    return os.getenv("CUCUMBER_PREVENTION", "1").strip() != "0"


def path(client: str) -> str:
    return os.path.join(cucumber_tools.cache_dir(client), FILE_NAME)


def columns_path(client: str) -> str:
    return os.path.join(cucumber_tools.cache_dir(client), COLUMNS_FILE)


def _read(file: str) -> dict:
    try:
        with open(file, encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def _write(file: str, data: dict) -> None:
    tmp = file + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=1)
    os.replace(tmp, file)


def load(client: str) -> dict:
    data = _read(path(client))
    if not isinstance(data.get("types"), dict):
        data = {"format": FORMAT, "types": {}}
    return data


def message(error: str) -> str:
    """Erreur sans le chemin du fichier ni la ligne en tête."""
    return _PATH.sub("", " ".join(str(error or "").split()), count=1)


def kind(error: str) -> str:
    """Type d'une erreur : message sans chemin, valeurs citées ni nombres (même contrôle, même règle)."""
    text = _QUOTED.sub("«…»", message(error))
    return re.sub(r"\d+", "#", text)[:160]


def record(client: str, errors: list, lot: int = None) -> int:
    """Compte les erreurs du premier contrôle d'un lot par type ; renvoie le nombre de types touchés. Ne lève jamais."""
    if not enabled() or not errors:
        return 0
    try:
        data = load(client)
        now = datetime.now().isoformat(timespec="seconds")
        touched = set()
        for error in errors:
            key = kind(error)
            if not key or key in touched:
                continue
            touched.add(key)
            entry = data["types"].setdefault(key, {"nombre": 0, "exemples": [], "steps": []})
            entry["nombre"] = int(entry.get("nombre") or 0) + 1
            entry["dernier"] = now
            entry["lot"] = lot
            example = message(error)[:400]
            entry["exemples"] = [e for e in entry.get("exemples") or [] if e != example][-(MAX_EXAMPLES - 1):] + [example]
            for step in _STEP.findall(error):
                if step not in entry["steps"]:
                    entry["steps"] = (entry["steps"] + [step])[-10:]
        if len(data["types"]) > MAX_KINDS:
            kept = sorted(data["types"].items(), key=lambda kv: (kv[1].get("nombre", 0), kv[1].get("dernier", "")))
            data["types"] = dict(kept[-MAX_KINDS:])
        data["format"] = FORMAT
        _write(path(client), data)
        return len(touched)
    except (OSError, TypeError, ValueError) as e:
        print(f"[PRÉVENTION] ⚠️ Relevé des contrôles non mis à jour ({e}).")
        return 0


def block(client: str, columns_by_step: dict = None) -> str:
    """Bloc « CONTRÔLES AUTOMATIQUES » des types d'erreur fréquents du client ; '' s'il n'y en a pas."""
    if not enabled():
        return ""
    frequent = sorted(((k, v) for k, v in load(client)["types"].items() if int(v.get("nombre") or 0) >= MIN_COUNT),
                      key=lambda kv: (-int(kv[1]["nombre"]), kv[1].get("dernier", "")))[:MAX_RULES]
    if not frequent:
        return ""
    lines = ["CONTRÔLES AUTOMATIQUES LES PLUS SOUVENT EN ÉCHEC CHEZ CE CLIENT (chaque fichier du lot y passera après "
             "toi ; respecte-les AVANT d'écrire, une erreur coûte un appel de correction) :"]
    for _, entry in frequent:
        lines.append(f"- {entry['nombre']} fois — ex. : {(entry.get('exemples') or ['?'])[-1][:300]}")
        for step in (entry.get("steps") or [])[-3:]:
            accepted = (columns_by_step or {}).get(step)
            if accepted:
                lines.append(f"  colonnes acceptées par le Standard pour « {step} » : "
                             + ", ".join(accepted[:MAX_COLUMNS]) + (" …" if len(accepted) > MAX_COLUMNS else ""))
    text = "\n".join(lines)
    return "\n" + (text if len(text) <= MAX_CHARS else text[:MAX_CHARS - 1] + "…") + "\n"


def write_columns(client: str, columns_by_step: dict) -> None:
    """Colonnes de DataTable du Standard par step, pour le contrôle par l'agent (cucumber_compile)."""
    if not enabled():
        return
    try:
        _write(columns_path(client), {"colonnes_par_step": columns_by_step or {}})
    except OSError as e:
        print(f"[PRÉVENTION] ⚠️ Colonnes du Standard non écrites pour le contrôle de l'agent ({e}).")


def agent_column_errors(client: str, worktree: str) -> list:
    """
    Colonnes de DataTable refusées dans les features ajoutées ou modifiées du worktree depuis le dernier commit (le
    lot en cours ; même contrôle que cucumber_verify.check_table_columns après le lot). [] sans colonnes_std.json.
    """
    if not enabled():
        return []
    data = _read(columns_path(client))
    if not data.get("colonnes_par_step"):
        return []
    from tools import cucumber_verify
    changed = cucumber_tools._git(worktree, "diff", "--name-only", "HEAD", "--", "*.feature").stdout.splitlines()
    changed += cucumber_tools._git(worktree, "ls-files", "--others", "--exclude-standard", "--",
                                   "*.feature").stdout.splitlines()
    features = sorted({f for f in changed if os.path.exists(os.path.join(worktree, *f.split("/")))})
    return cucumber_verify.check_table_columns(worktree, "HEAD", features,
                                               {"colonnes_par_step": data["colonnes_par_step"]})
