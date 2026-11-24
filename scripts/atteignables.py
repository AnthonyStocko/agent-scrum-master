"""Fonctions de premier niveau atteintes depuis chaque point d'entrée (page d'app.py, étape de main.py).

Usage : python scripts/atteignables.py app.py|main.py  -> docs/atteignables-<fichier>.csv (fonction;entrées)
Pour app.py, les points d'entrée sont les blocs « if PAGE == ... » de premier niveau ; le reste du code de
niveau module est l'entrée « commun ». Pour main.py, ce sont les branches « args.step == ... » de main() ;
le reste de main() est l'entrée « commun ». Lecture seule.
"""
import ast
import csv
import sys
from collections import defaultdict
from pathlib import Path

RACINE = Path(__file__).resolve().parent.parent


def noms_appeles(noeuds):
    out = set()
    for racine in noeuds:
        for n in ast.walk(racine):
            if isinstance(n, ast.Name):
                out.add(n.id)
    return out


DRAPEAUX = {"is_postmerge": "generate-cucumber-postmerge", "is_cucumber_client": "generate-cucumber-client",
            "is_besoin": "besoin", "is_cadrage_answers": "cadrage-reponses"}


def etapes_du_test(test):
    """Valeurs de « X == 'v' » ou « X in (...) » d'un test portant sur PAGE / args.step, ou drapeau is_* d'étape
    (« not is_* » n'est pas une étape)."""
    vals = set()
    if isinstance(test, ast.Name) and test.id in DRAPEAUX:
        return {DRAPEAUX[test.id]}
    if isinstance(test, ast.BoolOp) and isinstance(test.op, ast.And):
        for v in test.values:
            if isinstance(v, ast.Name) and v.id in DRAPEAUX:
                return {DRAPEAUX[v.id]}
    for n in ast.walk(test):
        if isinstance(n, ast.Compare) and isinstance(n.comparators[0], (ast.Constant, ast.Tuple, ast.List, ast.Set)):
            g = ast.unparse(n.left)
            if g in ("PAGE", "args.step", "step"):
                c = n.comparators[0]
                elts = c.elts if hasattr(c, "elts") else [c]
                vals |= {e.value for e in elts if isinstance(e, ast.Constant)}
    return vals


def main():
    fichier = sys.argv[1]
    arbre = ast.parse((RACINE / fichier).read_text(encoding="utf-8"))
    defs = {n.name: n for n in arbre.body if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))}
    entrees = defaultdict(list)
    if fichier == "app.py":
        for n in arbre.body:
            if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                continue
            vals = etapes_du_test(n.test) if isinstance(n, ast.If) else set()
            for v in vals or {"commun"}:
                entrees[v].append(n)
    else:
        corps = defs["main"].body

        def parcours(stmts, contexte):
            for s in stmts:
                vals = etapes_du_test(s.test) if isinstance(s, ast.If) else set()
                if vals and contexte == "commun":
                    for v in vals:
                        entrees[v].append(s.test)
                        parcours(s.body, v)
                    parcours(s.orelse, "commun")
                elif contexte == "commun" and isinstance(s, (ast.If, ast.For, ast.While, ast.With, ast.Try)):
                    entete = [getattr(s, a) for a in ("test", "iter", "target") if hasattr(s, a)]
                    entete += [i for i in getattr(s, "items", [])]
                    entrees[contexte].extend(entete)
                    for champ in ("body", "orelse", "finalbody"):
                        parcours(getattr(s, champ, []), contexte)
                    for h in getattr(s, "handlers", []):
                        parcours(h.body, contexte)
                else:
                    entrees[contexte].append(s)
        parcours(corps, "commun")
        for n in arbre.body:
            if not isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                entrees["commun"].append(n)
    atteint = defaultdict(set)
    for e, noeuds in entrees.items():
        pile = list(noms_appeles(noeuds) & defs.keys() - ({"main"} if fichier == "main.py" else set()))
        while pile:
            f = pile.pop()
            if e in atteint[f]:
                continue
            atteint[f].add(e)
            pile += list(noms_appeles([defs[f]]) & defs.keys() - {"main"} if fichier == "main.py" else noms_appeles([defs[f]]) & defs.keys())
    sortie = RACINE / "docs" / f"atteignables-{Path(fichier).stem}.csv"
    with open(sortie, "w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh, delimiter=";")
        w.writerow(["fonction", "debut", "fin", "entrees"])
        for nom, n in defs.items():
            w.writerow([nom, n.lineno, n.end_lineno, "|".join(sorted(atteint.get(nom, {"aucune"})))])
    print(f"écrit : {sortie.relative_to(RACINE)}")


if __name__ == "__main__":
    main()
