"""Réécrit les imports de modules déplacés de `tools` vers un paquet (ex. agent_socle), sans toucher au reste du code.

Usage : python scripts/deplacer_imports.py PAQUET MODULE[,MODULE…] FICHIER_OU_DOSSIER…
  - `from tools import a, b` : les modules déplacés passent dans `from PAQUET import …` (ligne séparée) ;
  - `from tools.X import …` : `tools.X` devient `PAQUET.X` ;
  - chaînes "tools.X" / "tools.X.attr" (monkeypatch, mock.patch) : `tools.X` devient `PAQUET.X`.
L'indentation et le commentaire de fin de l'instruction sont gardés. Affiche le nombre de réécritures par fichier.
"""
import ast
import re
import sys
from pathlib import Path


def fichiers(cibles):
    for c in cibles:
        p = Path(c)
        if p.is_dir():
            yield from (f for f in sorted(p.rglob("*.py")) if "__pycache__" not in f.parts)
        elif p.suffix == ".py":
            yield p


def nom(alias):
    return alias.name + (f" as {alias.asname}" if alias.asname else "")


def ligne_import(indent, module, noms, commentaire):
    texte = f"{indent}from {module} import {', '.join(noms)}"
    if len(texte) > 116:
        texte = f"{indent}from {module} import (" + ",\n".join(
            (indent + "    " if i else "") + n for i, n in enumerate(noms)) + ")"
    return texte + (f"  {commentaire}" if commentaire else "")


def reecrire(src, paquet, modules):
    lignes = src.splitlines(keepends=True)
    fin_ligne = "\r\n" if src.count("\r\n") > src.count("\n") / 2 else "\n"
    remplacements = []  # (debut, fin, texte)
    for n in ast.walk(ast.parse(src)):
        if not isinstance(n, ast.ImportFrom) or n.level:
            continue
        premiere = lignes[n.lineno - 1]
        indent = premiere[:len(premiere) - len(premiere.lstrip())]
        derniere = lignes[n.end_lineno - 1].rstrip("\r\n")
        commentaire = derniere[derniere.index("#", n.end_col_offset):].strip() if "#" in derniere[n.end_col_offset:] else ""
        if n.module == "tools":
            dep = [a for a in n.names if a.name in modules]
            if not dep:
                continue
            reste = [a for a in n.names if a.name not in modules]
            nouveau = []
            if reste:
                nouveau.append(ligne_import(indent, "tools", [nom(a) for a in reste], ""))
            nouveau.append(ligne_import(indent, paquet, [nom(a) for a in dep], commentaire))
            remplacements.append((n.lineno, n.end_lineno, fin_ligne.join(nouveau) + fin_ligne))
        elif n.module and n.module.startswith("tools.") and n.module.split(".")[1] in modules:
            bloc = "".join(lignes[n.lineno - 1:n.end_lineno])
            remplacements.append((n.lineno, n.end_lineno, bloc.replace(f"from {n.module} ", f"from {paquet}.{n.module[6:]} ", 1)))
    for debut, fin, texte in sorted(remplacements, reverse=True):
        lignes[debut - 1:fin] = [texte]
    sortie = "".join(lignes)
    motif = re.compile(r"""(["'])tools\.(""" + "|".join(map(re.escape, modules)) + r""")\b""")
    sortie, nb_chaines = motif.subn(lambda m: f"{m.group(1)}{paquet}.{m.group(2)}", sortie)
    return sortie, len(remplacements) + nb_chaines


def main():
    paquet, modules = sys.argv[1], set(sys.argv[2].split(","))
    total = 0
    for f in fichiers(sys.argv[3:]):
        brut = f.read_bytes().decode("utf-8")
        nouveau, nb = reecrire(brut, paquet, modules)
        if nb:
            f.write_bytes(nouveau.encode("utf-8"))
            print(f"{f}: {nb}")
            total += nb
    print(f"total : {total}")


if __name__ == "__main__":
    main()
