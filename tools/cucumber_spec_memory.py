"""
Mémoire par spécifique entre générations (0 token).

Un spécifique peut être généré plusieurs fois (relance, nouvelle génération, complément). Chaque génération
repartait de zéro : mêmes explorations, mêmes hypothèses déjà réfutées, mêmes impasses. Ce module garde, par
spécifique, ce que les générations précédentes ont établi et le donne au lot suivant qui le traite :

- résultat de chaque génération (type de résultat, scénarios verts / en échec) et coût cumulé ;
- causes ouvertes : blocages hors test prouvés (avec la condition de réactivation), échecs restés sans cause ;
- pistes écartées : causes supposées réfutées par un « même échec » (dossier de passation, cucumber_handover) ;
- hypothèses non vérifiées : à vérifier en premier, pas à tenir pour acquises ;
- décisions en attente : valeurs ou désactivations soumises à l'utilisateur, à ne pas changer sans sa décision ;
- causes prouvées des corrections réussies (ce qui a marché).

Fichier : .tmp/cucumber/<client>/memoire_specifiques.json ({"format", "specifiques": {id: {"libelle",
"generations": [...]}}}), 5 générations gardées par spécifique. Alimenté à la fin de chaque lot (record_lot), lu à
la préparation du prompt du lot (block, MAX_CHARS au plus). Aucune donnée n'est inventée : tout vient de l'état du
lot. CUCUMBER_SPEC_MEMORY=0 : ni enregistrement ni injection.
"""
import json
import os
from datetime import datetime

from tools import cucumber_tools

FORMAT = 1
FILE_NAME = "memoire_specifiques.json"
MAX_GENERATIONS = 5        # générations gardées par spécifique
MAX_ITEMS = 6              # éléments gardés par rubrique et par génération
MAX_CHARS = 4000           # bloc injecté au plus (tous spécifiques du lot)
_SHORT = 220
GREEN = "pertinent"


def enabled() -> bool:
    return os.getenv("CUCUMBER_SPEC_MEMORY", "1").strip() != "0"


def path(client: str) -> str:
    return os.path.join(cucumber_tools.cache_dir(client), FILE_NAME)


def load(client: str) -> dict:
    try:
        with open(path(client), encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, ValueError):
        data = {}
    if not isinstance(data, dict) or not isinstance(data.get("specifiques"), dict):
        data = {"format": FORMAT, "specifiques": {}}
    return data


def save(client: str, data: dict) -> None:
    data["format"] = FORMAT
    target = path(client)
    tmp = target + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=1)
    os.replace(tmp, target)


def _short(text, limit: int = _SHORT) -> str:
    text = " ".join(str(text or "").split())
    return text if len(text) <= limit else text[:limit - 1] + "…"


def _add(items: list, text: str) -> None:
    text = _short(text)
    if text and text not in items and len(items) < MAX_ITEMS:
        items.append(text)


def _spec_of_tags(lot: dict) -> dict:
    """{tag: spécifique} : scénario du tag (lot["tags"]) → spécifique (lot["indicateurs"]) ; lot d'un seul spécifique :
    tout lui revient. Tag non rattaché : ''."""
    ids = [i for i in lot.get("ids") or [] if i] or ([lot["spec_correction"]] if lot.get("spec_correction") else [])
    by_scenario = {}
    for s in (lot.get("indicateurs") or {}).get("scenarios") or []:
        if isinstance(s, dict) and s.get("spec"):
            by_scenario[f"{s.get('fichier', '')}::{str(s.get('titre', '')).strip()}"] = s["spec"]
    tags = dict(lot.get("tags") or {})
    for tag, item in ((lot.get("execution") or {}).get("scenarios") or {}).items():
        tags.setdefault(tag, (item or {}).get("id", ""))
    out = {}
    for tag, scenario in tags.items():
        out[tag] = by_scenario.get(scenario) or (ids[0] if len(ids) == 1 else "")
    return out


def _generation(lot: dict, spec_id: str, tags: list, outcome: dict, scenario_ids: set) -> dict:
    scenarios = (lot.get("execution") or {}).get("scenarios") or {}
    mine = {t: scenarios[t] for t in tags if t in scenarios}
    entry = {"date": lot.get("fin") or datetime.now().isoformat(timespec="seconds"), "lot": lot.get("numero"),
             "type_lot": lot.get("type") or "generation", "resultat": (outcome or {}).get("type", ""),
             "message": _short((outcome or {}).get("message", "")),
             "verts": sum(1 for v in mine.values() if (v or {}).get("statut") == GREEN),
             "en_echec": sum(1 for v in mine.values() if (v or {}).get("statut") not in (GREEN, None)),
             "causes_ouvertes": [], "pistes_ecartees": [], "hypotheses": [], "decisions_en_attente": [],
             "ce_qui_a_marche": []}
    if lot.get("erreur"):
        _add(entry["causes_ouvertes"], f"lot en erreur : {lot['erreur']}")
    # Désactivations (blocage hors test, prouvé ou non) : cause et condition de réactivation
    for item in lot.get("a_revoir") or []:
        if isinstance(item, dict) and item.get("id") in scenario_ids:
            _add(entry["causes_ouvertes"], f"{item.get('titre', '')} : {item.get('cause', '')} "
                 + ("(prouvé" + (f" : {_short(item.get('preuve'), 120)}" if item.get("preuve") else "") + ")"
                    if item.get("prouvee") else "(non prouvé)")
                 + (f" — {item['reactivation']}" if item.get("reactivation") else ""))
    # Échecs restés sans désactivation
    disabled = {i.get("id") for i in lot.get("a_revoir") or [] if isinstance(i, dict)}
    for tag, item in mine.items():
        if (item or {}).get("statut") not in (GREEN, None) and item.get("id") not in disabled:
            _add(entry["causes_ouvertes"], f"{str(item.get('id', tag)).split('::')[-1]} : {item.get('statut')} — "
                                           f"{_short(item.get('motif'), 160)}")
    # Dossier de passation : causes supposées réfutées, hypothèses en suspens, causes prouvées qui ont marché
    for tag in tags:
        attempts = (lot.get("passation") or {}).get(tag) or []
        for attempt in attempts:
            refuted = str(attempt.get("resultat", "")).startswith("même échec")
            for cause in attempt.get("causes") or []:
                if refuted and not cause.get("prouvee"):
                    _add(entry["pistes_ecartees"], f"{cause.get('cause')} (corrigé : {cause.get('correction')}, "
                                                   f"même échec ensuite)")
                elif attempt.get("resultat") == "vert":
                    _add(entry["ce_qui_a_marche"], f"{cause.get('cause')} → {cause.get('correction')}")
        if attempts and not str(attempts[-1].get("resultat", "")).startswith(("vert", "même échec")):
            for hypothesis in attempts[-1].get("hypotheses") or []:
                _add(entry["hypotheses"], hypothesis)
    # Restantes de la correction (format détaillé) : hypothèses, décisions, causes hors test prouvées
    for item in (lot.get("correction") or {}).get("restantes_detail") or []:
        if not isinstance(item, dict) or (item.get("tag") and item.get("tag") not in tags):
            continue
        nature = str(item.get("nature", ""))
        if nature == "a_verifier":
            _add(entry["hypotheses"], item.get("cause"))
        elif nature == "validation_humaine":
            value = item.get("valeur") or {}
            _add(entry["decisions_en_attente"], f"{item.get('cause', '')}"
                 + (f" — {value.get('step', '')} « {value.get('avant', '')} » → « {value.get('apres', '')} »"
                    if isinstance(value, dict) and value else ""))
        elif nature == "hors_test_prouvee":
            _add(entry["causes_ouvertes"], f"{item.get('cause', '')} (prouvé)"
                 + (f" — {item['reactivation']}" if item.get("reactivation") else ""))
    for scenario in lot.get("decisions_en_attente") or []:
        if scenario in scenario_ids:
            _add(entry["decisions_en_attente"], f"{str(scenario).split('::')[-1]} : modification en attente de "
                                                f"décision humaine")
    for motif in lot.get("arret") or []:
        _add(entry["decisions_en_attente"], f"arrêt du lot : {motif}")
    return entry


def record_lot(client: str, lot: dict, outcomes: dict = None, labels: dict = None) -> list:
    """
    Ajoute la génération du lot à la mémoire de chacun de ses spécifiques ; `outcomes` : state["generations"]
    (résultat de chaque tâche), `labels` : {id: libellé}. Renvoie les spécifiques mis à jour. Ne lève jamais.
    """
    if not enabled() or not isinstance(lot, dict):
        return []
    try:
        ids = [i for i in lot.get("ids") or [] if i] or (
            [lot["spec_correction"]] if lot.get("spec_correction") else [])
        if not ids:
            return []
        spec_of = _spec_of_tags(lot)
        scenarios = (lot.get("execution") or {}).get("scenarios") or {}
        data = load(client)
        for spec_id in ids:
            tags = [t for t, s in spec_of.items() if s == spec_id]
            scenario_ids = {(scenarios.get(t) or {}).get("id") or (lot.get("tags") or {}).get(t) for t in tags}
            entry = _generation(lot, spec_id, tags, (outcomes or {}).get(spec_id), scenario_ids)
            memory = data["specifiques"].setdefault(spec_id, {"libelle": "", "generations": []})
            memory["libelle"] = (labels or {}).get(spec_id) or memory.get("libelle", "")
            memory["generations"] = (memory.get("generations") or [])[-(MAX_GENERATIONS - 1):] + [entry]
        save(client, data)
        return ids
    except (OSError, TypeError, ValueError, AttributeError) as e:
        print(f"[MÉMOIRE] ⚠️ Mémoire des spécifiques non mise à jour ({e}).")
        return []


def _spec_lines(spec_id: str, memory: dict, cost: float) -> list:
    generations = memory.get("generations") or []
    last = generations[-1]
    lines = [f"- {memory.get('libelle') or spec_id} ({spec_id}) : {len(generations)} génération(s) précédente(s)"
             + (f", coût cumulé {cost:.2f} $" if cost else "")
             + f" ; dernière le {str(last.get('date', ''))[:10]} : {last.get('resultat') or '?'}"
             + f", {last.get('verts', 0)} scénario(s) vert(s), {last.get('en_echec', 0)} en échec"
             + (f" — {last['message']}" if last.get("message") else "")]
    # Rubriques : la plus récente d'abord, sans doublon d'une génération à l'autre
    for key, title in (("causes_ouvertes", "Causes ouvertes (dernière génération)"),
                       ("decisions_en_attente", "Décisions en attente (ne change pas ces valeurs sans décision)"),
                       ("pistes_ecartees", "Pistes écartées (réfutées : ne les retente pas)"),
                       ("hypotheses", "Hypothèses non vérifiées (à prouver d'abord)"),
                       ("ce_qui_a_marche", "Ce qui a marché")):
        source = [last] if key in ("causes_ouvertes", "decisions_en_attente") else reversed(generations)
        items = []
        for generation in source:
            for item in generation.get(key) or []:
                if item not in items:
                    items.append(item)
        if items:
            lines.append(f"  {title} :")
            lines += [f"    • {item}" for item in items[:MAX_ITEMS]]
    return lines


def block(client: str, spec_ids, costs: dict = None) -> str:
    """Bloc « MÉMOIRE DES GÉNÉRATIONS PRÉCÉDENTES » des spécifiques `spec_ids` ; '' s'il n'y a rien."""
    if not enabled():
        return ""
    specifics = load(client)["specifiques"]
    sections = []
    for spec_id in spec_ids or []:
        memory = specifics.get(spec_id) or {}
        if memory.get("generations"):
            cost = float(((costs or {}).get(spec_id) or {}).get("total") or 0)
            sections.append("\n".join(_spec_lines(spec_id, memory, cost)))
    if not sections:
        return ""
    head = ("\nMÉMOIRE DES GÉNÉRATIONS PRÉCÉDENTES (ce qui est déjà établi pour ces spécifiques ; repars-en au lieu "
            "de refaire l'exploration) :\nUne piste écartée a été réfutée par l'exécution : ne la retente pas. Une "
            "hypothèse non vérifiée n'est pas un fait. Une décision en attente appartient à l'utilisateur. Une cause "
            "ouverte prouvée hors test ne se corrige pas dans le test.\n")
    text = head
    budget = max(400, (MAX_CHARS - len(head)) // len(sections))
    for section in sections:
        text += (section if len(section) <= budget else section[:budget - 1] + "…") + "\n"
    return text
