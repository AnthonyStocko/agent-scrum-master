"""Inventaire du code par analyse syntaxique (ast) : produit docs/inventaire.csv.

Une ligne par fonction ou classe de premier niveau (et par méthode) des fichiers Python du dépôt (hors tests,
venv) : fichier, nom, lignes début-fin, modules internes importés par le fichier, appels faits, clés
st.session_state et variables d'environnement lues. Une ligne « <module> » par fichier porte le code de niveau
module (imports, constantes, aiguillage des pages). Lecture seule : ne modifie aucun fichier source.
"""
import ast
import csv
import os
import sys
from pathlib import Path

RACINE = Path(__file__).resolve().parent.parent
EXCLUS = {"venv", ".venv", "tests", "__pycache__", ".git", ".tmp", "scripts"}


def fichiers():
    trouves = []
    for dossier, sous, noms in os.walk(RACINE):
        sous[:] = [d for d in sous if d not in EXCLUS and not d.startswith(".")]
        trouves += [Path(dossier) / n for n in noms if n.endswith(".py")]
    yield from sorted(trouves)


def nom_module(p):
    return ".".join(p.relative_to(RACINE).with_suffix("").parts)


def imports_internes(arbre, internes):
    trouves = set()
    for n in ast.walk(arbre):
        if isinstance(n, ast.Import):
            noms = [a.name for a in n.names]
        elif isinstance(n, ast.ImportFrom) and n.module:
            noms = [n.module] + [f"{n.module}.{a.name}" for a in n.names]
        else:
            continue
        for nom in noms:
            if nom in internes:
                trouves.add(nom)
    return trouves


def texte(n):
    try:
        return ast.unparse(n)
    except Exception:
        return ""


def analyse(noeud, corps_seul=False):
    appels, cles, env = set(), set(), set()
    cibles = noeud.body if corps_seul else [noeud]
    for racine in cibles:
        for n in ast.walk(racine):
            if isinstance(n, ast.Call):
                f = texte(n.func)
                if f and len(f) < 80:
                    appels.add(f)
                if f in ("os.getenv", "os.environ.get", "getenv", "_env", "env") and n.args \
                        and isinstance(n.args[0], ast.Constant) and isinstance(n.args[0].value, str):
                    env.add(n.args[0].value)
                if f.endswith("session_state.get") and n.args and isinstance(n.args[0], ast.Constant):
                    cles.add(str(n.args[0].value))
            elif isinstance(n, ast.Subscript):
                v = texte(n.value)
                if isinstance(n.slice, ast.Constant) and isinstance(n.slice.value, str):
                    if v.endswith("session_state"):
                        cles.add(n.slice.value)
                    elif v == "os.environ":
                        env.add(n.slice.value)
            elif isinstance(n, ast.Attribute) and texte(n.value).endswith("st.session_state"):
                if n.attr not in ("get", "setdefault", "pop", "keys", "items", "update", "clear"):
                    cles.add(n.attr)
    return appels, cles, env


def main():
    internes = {nom_module(p) for p in fichiers()}
    internes |= {m.rsplit(".", 1)[0] for m in internes if "." in m}
    sortie = RACINE / "docs" / "inventaire.csv"
    with open(sortie, "w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh, delimiter=";")
        w.writerow(["fichier", "nom", "type", "debut", "fin", "lignes", "imports_internes", "appels",
                    "session_state", "env"])
        for p in fichiers():
            rel = p.relative_to(RACINE).as_posix()
            src = p.read_text(encoding="utf-8", errors="replace")
            try:
                arbre = ast.parse(src)
            except SyntaxError as e:
                print(f"illisible : {rel} ({e})", file=sys.stderr)
                continue
            imp = "|".join(sorted(imports_internes(arbre, internes)))
            hors_def = ast.Module(body=[n for n in arbre.body if not isinstance(
                n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))], type_ignores=[])
            a, c, e = analyse(hors_def, corps_seul=True)
            w.writerow([rel, "<module>", "module", 1, len(src.splitlines()), len(src.splitlines()), imp,
                        "|".join(sorted(a)), "|".join(sorted(c)), "|".join(sorted(e))])
            for n in arbre.body:
                if not isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                    continue
                debut = min([d.lineno for d in n.decorator_list] + [n.lineno])
                a, c, e = analyse(n)
                typ = "classe" if isinstance(n, ast.ClassDef) else "fonction"
                w.writerow([rel, n.name, typ, debut, n.end_lineno, n.end_lineno - debut + 1, "",
                            "|".join(sorted(a)), "|".join(sorted(c)), "|".join(sorted(e))])
    print(f"écrit : {sortie.relative_to(RACINE)}")


if __name__ == "__main__":
    main()
