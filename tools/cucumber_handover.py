"""
Dossier de passation des corrections après exécution (0 token).

Chaque correction après exécution part dans une session neuve (le contexte de la session du lot, relu à chaque
tour, faisait l'essentiel du coût). Pour qu'elle ne refasse pas le chemin de la précédente, le lot garde, par
scénario (lot["passation"][tag]), ce que chaque tentative a établi :

- la cause retenue, prouvée ou non, et la correction faite ;
- les preuves obtenues en base (tables, conclusion ; requête abrégée) ;
- les hypothèses non prouvées et la correction identifiée mais non appliquée ;
- le résultat au run suivant : vert, même échec (hypothèse réfutée), nouvel échec, non relancé.

Le bloc rendu (block) est ajouté au prompt de la tentative suivante et de l'analyse du dernier run, avec les
commandes base déjà lancées en erreur (lot["base"]) pour ne pas les répéter. Il est borné (MAX_CHARS) : les
tentatives les plus récentes passent d'abord. Le dossier est gardé dans l'état du lot : « Reprendre » le retrouve.

CUCUMBER_HANDOVER=0 : ni dossier ni session neuve forcée (comportement antérieur, pour comparer la consommation).
"""
import os
import re

from tools import cucumber_fix_budget

MAX_CHARS = 5000           # bloc injecté au plus
MAX_ATTEMPTS = 6           # tentatives gardées par scénario
_SHORT = 240
_OMITTED = "  • (tentatives plus anciennes omises)"


def enabled() -> bool:
    return os.getenv("CUCUMBER_HANDOVER", "1").strip() != "0"


def _short(text, limit: int = _SHORT) -> str:
    text = " ".join(str(text or "").split())
    return text if len(text) <= limit else text[:limit - 1] + "…"


def _for_tag(items, tag: str) -> list:
    return [i for i in items or [] if isinstance(i, dict) and str(i.get("tag", "")).strip() == tag]


def record_attempt(lot: dict, step: str, fix_result: dict, sent: dict) -> None:
    """
    Note dans lot["passation"] ce que la tentative `step` (« correction 2 », « analyse du dernier run ») a établi pour
    chaque scénario envoyé (`sent` : {tag: résultat avant la correction}).
    """
    if not enabled() or not isinstance(lot, dict):
        return
    fix_result = fix_result if isinstance(fix_result, dict) else {}
    folder = lot.setdefault("passation", {})
    for tag, before in (sent or {}).items():
        corrections = _for_tag(fix_result.get("corrections"), tag)
        remaining = _for_tag(fix_result.get("restantes"), tag)
        proofs = _for_tag(fix_result.get("preuves_base"), tag)
        entry = {
            "etape": step,
            "echec": _short((before or {}).get("motif", ""), 300),
            "causes": [{"cause": _short(c.get("cause")), "correction": _short(c.get("correction")),
                        "prouvee": isinstance(c.get("cause_prouvee"), dict)
                        and bool(str(c["cause_prouvee"].get("erreur", "")).strip())}
                       for c in corrections],
            "preuves": [{"tables": [str(t) for t in (p.get("tables") or [])][:5],
                         "conclusion": _short(p.get("conclusion"), 80), "resultat": _short(p.get("resultat"), 160),
                         "requete": _short(p.get("requete"), 200)} for p in proofs][:6],
            "hypotheses": [_short(r.get("cause")) for r in remaining
                           if str(r.get("nature", "")).strip() == "a_verifier" and r.get("cause")],
            "a_faire": [_short(r.get("correction_a_faire")) for r in remaining if r.get("correction_a_faire")],
            "signature": list(cucumber_fix_budget.signature(before or {})),
        }
        if not fix_result:
            entry["causes"] = [{"cause": "réponse illisible ou absente (tours épuisés, erreur d'appel)",
                                "correction": "", "prouvee": False}]
        attempts = folder.setdefault(tag, [])
        attempts.append(entry)
        del attempts[:-MAX_ATTEMPTS]


def record_outcome(lot: dict, results: dict, rerun: set) -> None:
    """
    Résultat de la dernière tentative de chaque scénario après revérification : `results` {tag: résultat},
    `rerun` : tags relancés (les autres sont « non relancé »).
    """
    if not enabled() or not isinstance(lot, dict):
        return
    for tag, attempts in (lot.get("passation") or {}).items():
        last = attempts[-1] if attempts else None
        if not last or last.get("resultat"):
            continue
        after = (results or {}).get(tag)
        if tag not in (rerun or set()) or after is None:
            last["resultat"] = "non relancé"
        elif after.get("statut") == "pertinent":
            last["resultat"] = "vert"
        elif list(cucumber_fix_budget.signature(after)) == last.get("signature"):
            last["resultat"] = "même échec : hypothèse réfutée"
        else:
            last["resultat"] = "nouvel échec : " + _short(after.get("motif", ""), 200)


def _attempt_lines(entry: dict) -> list:
    lines = [f"  • {entry.get('etape', '?')} — échec traité : {entry.get('echec') or '?'}"]
    for c in entry.get("causes") or []:
        lines.append(f"    cause {'PROUVÉE' if c.get('prouvee') else 'supposée (non prouvée)'} : {c.get('cause')}"
                     + (f" ; correction : {c['correction']}" if c.get("correction") else ""))
    for p in entry.get("preuves") or []:
        lines.append(f"    preuve base [{', '.join(p.get('tables') or []) or '?'}] {p.get('conclusion', '')}"
                     + (f" — {p['resultat']}" if p.get("resultat") else "")
                     + (f" (requête : {p['requete']})" if p.get("requete") else ""))
    for h in entry.get("hypotheses") or []:
        lines.append(f"    hypothèse non vérifiée : {h}")
    for a in entry.get("a_faire") or []:
        lines.append(f"    correction identifiée, non appliquée : {a}")
    lines.append(f"    → résultat : {entry.get('resultat') or 'en attente du run'}")
    return lines


def _db_errors(lot: dict) -> list:
    """Commandes base en erreur des corrections précédentes (sans texte de requête : non journalisé)."""
    seen, out = set(), []
    for e in lot.get("base") or []:
        if not isinstance(e, dict) or e.get("ok", True) or not e.get("erreur"):
            continue
        key = (e.get("commande"), tuple(e.get("tables") or []), re.sub(r"\d+", "#", e["erreur"])[:120])
        if key in seen:
            continue
        seen.add(key)
        out.append(f"  • {e.get('commande', '?')} [{', '.join(e.get('tables') or []) or '?'}] : "
                   f"{_short(e['erreur'], 200)}")
    return out[-8:]


def block(lot: dict, tags) -> str:
    """Bloc « DOSSIER DE PASSATION » pour les scénarios `tags` ; '' s'il n'y a rien à transmettre."""
    if not enabled() or not isinstance(lot, dict):
        return ""
    folder = lot.get("passation") or {}
    sections = []
    for tag in tags or []:
        attempts = folder.get(tag) or []
        if not attempts:
            continue
        lines = [f"- {tag} : {len(attempts)} tentative(s) précédente(s)"]
        for entry in attempts:
            lines += _attempt_lines(entry)
        sections.append(lines)
    errors = _db_errors(lot)
    if not sections and not errors:
        return ""
    head = ("\n\nDOSSIER DE PASSATION (tentatives précédentes, dans des sessions que tu ne vois pas) :\n"
            "Repars de ce qui est établi. Une cause « supposée » suivie de « même échec » est RÉFUTÉE : ne la retente "
            "pas. Une preuve base citée est acquise : ne refais pas la requête, sauf pour lire les données du "
            "nouveau run. Une correction identifiée non appliquée : applique-la d'abord.\n")
    text = head
    for lines in sections:
        # Bornage : les tentatives les plus anciennes d'un scénario tombent d'abord (la dernière reste toujours)
        while len(text) + len("\n".join(lines)) > MAX_CHARS:
            starts = [i for i, line in enumerate(lines) if line.startswith("  •") and line != _OMITTED]
            if len(starts) < 2:
                break
            lines = [lines[0], _OMITTED] + lines[starts[1]:]
        text += "\n".join(lines) + "\n"
    if errors:
        text += "Commandes base déjà en erreur (ne pas les relancer telles quelles) :\n" + "\n".join(errors) + "\n"
    return text[:MAX_CHARS + 600]
