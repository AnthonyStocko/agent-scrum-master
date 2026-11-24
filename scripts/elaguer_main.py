"""Élague `main()` de main.py pour un agent : retire les branches des étapes qui ne sont pas les siennes.

Usage (module) : elaguer(src, etapes, connus) -> src
  - etapes : étapes `--step` de l'agent ; `args.step == X` est faux si X n'en est pas, `args.step in (...)` faux si
    disjoint, vrai si toutes ses étapes y sont ;
  - connus : {nom: bool} des drapeaux fixés (ex. {"is_cucumber_client": False, "jira_only": True}).
Un `if` toujours faux est retiré (son `elif`/`else` prend sa place), un `if` toujours vrai est remplacé par son corps
(désindenté). Seul `main()` est touché ; aucune autre ligne gardée n'est modifiée, sauf `elif` → `if` et
l'indentation des corps remontés. Le texte et les commentaires sont conservés.
"""
import ast

INCONNU = None


def vide(n, connus):
    """Expression connue pour valoir {} ou None (dictionnaire vide constant, `.get` dessus, `a or {}` de tels)."""
    if isinstance(n, ast.Dict) and not n.keys:
        return True
    if isinstance(n, ast.Name):
        return connus.get(n.id, 0) == {}
    if isinstance(n, ast.BoolOp) and isinstance(n.op, ast.Or):
        return all(vide(v, connus) for v in n.values)
    return (isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute) and n.func.attr == "get"
            and vide(n.func.value, connus))


def valeur(n, etapes, connus):
    """Valeur booléenne connue de l'expression, sinon None."""
    if vide(n, connus):
        return False
    if isinstance(n, ast.Name) and n.id in connus:
        return bool(connus[n.id])
    if isinstance(n, ast.UnaryOp) and isinstance(n.op, ast.Not):
        v = valeur(n.operand, etapes, connus)
        return None if v is None else not v
    if isinstance(n, ast.BoolOp):
        vals = [valeur(v, etapes, connus) for v in n.values]
        if isinstance(n.op, ast.And):
            return False if False in vals else (True if all(v is True for v in vals) else None)
        return True if True in vals else (False if all(v is False for v in vals) else None)
    if (isinstance(n, ast.Compare) and len(n.ops) == 1 and isinstance(n.left, ast.Attribute)
            and n.left.attr == "step" and isinstance(n.left.value, ast.Name) and n.left.value.id == "args"):
        droite = n.comparators[0]
        if isinstance(n.ops[0], ast.Eq) and isinstance(droite, ast.Constant):
            return False if droite.value not in etapes else (True if set(etapes) == {droite.value} else None)
        if isinstance(n.ops[0], ast.In) and isinstance(droite, (ast.Tuple, ast.List, ast.Set)):
            vals = {e.value for e in droite.elts if isinstance(e, ast.Constant)}
            return False if not vals & set(etapes) else (True if set(etapes) <= vals else None)
    return INCONNU


def _premier(fn, etapes, connus, src):
    """Dernier `if` (dans l'ordre du texte) dont le test est connu, avec sa valeur et s'il est un `elif`."""
    lignes = src.split("\n")
    trouves = []
    for n in ast.walk(fn):
        if isinstance(n, ast.If):
            v = valeur(n.test, etapes, connus)
            if v is not None:
                trouves.append((n.lineno, n, v, lignes[n.lineno - 1].lstrip().startswith("elif")))
    return max(trouves, key=lambda t: t[0])[1:] if trouves else None


def _ligne_else(lignes, n):
    """Index (0) de la ligne `else:` du nœud (orelse non elif)."""
    i = n.orelse[0].lineno - 2
    while not lignes[i].lstrip().startswith("else"):
        i -= 1
    return i


def _desindenter(bloc):
    return [l[4:] if l.startswith("    ") else l for l in bloc]


def _etape(src, etapes, connus):
    arbre = ast.parse(src)
    fn = next(n for n in arbre.body if isinstance(n, ast.FunctionDef) and n.name == "main")
    trouve = _premier(fn, etapes, connus, src)
    if not trouve:
        return None
    n, v, est_elif = trouve
    lignes = src.split("\n")
    debut, fin = n.lineno - 1, n.end_lineno  # tranche [debut, fin)
    fin_test = n.test.end_lineno            # ligne (1) du ':' de l'en-tête
    assert n.body[0].lineno > fin_test, f"corps sur la ligne de l'en-tête (l. {n.lineno})"
    orelse_elif = bool(n.orelse) and isinstance(n.orelse[0], ast.If) and \
        lignes[n.orelse[0].lineno - 1].lstrip().startswith("elif")
    if v:
        fin_corps = (_ligne_else(lignes, n) if n.orelse and not orelse_elif
                     else n.orelse[0].lineno - 1 if orelse_elif else fin)
        corps = lignes[fin_test:fin_corps]
        if est_elif:
            indent = lignes[debut][:len(lignes[debut]) - len(lignes[debut].lstrip())]
            nouveau = [indent + "else:"] + corps
        else:
            nouveau = _desindenter(corps)
        return "\n".join(lignes[:debut] + nouveau + lignes[fin:])
    if not n.orelse:
        while not est_elif and debut > 0 and lignes[debut - 1].lstrip().startswith("#") and \
                lignes[debut - 1][:len(lignes[debut]) - len(lignes[debut].lstrip())].strip() == "" and \
                len(lignes[debut - 1]) - len(lignes[debut - 1].lstrip()) == len(lignes[debut]) - len(lignes[debut].lstrip()):
            debut -= 1
        return "\n".join(lignes[:debut] + lignes[fin:])
    if orelse_elif:
        suite = n.orelse[0].lineno - 1
        reste = lignes[suite:fin]
        if not est_elif:
            reste[0] = reste[0].replace("elif", "if", 1)
        return "\n".join(lignes[:debut] + reste + lignes[fin:])
    i_else = _ligne_else(lignes, n)
    if est_elif:
        return "\n".join(lignes[:debut] + lignes[i_else:])
    return "\n".join(lignes[:debut] + _desindenter(lignes[i_else + 1:fin]) + lignes[fin:])


def _decaler(src, lignes_offsets, ligne, col):
    return lignes_offsets[ligne - 1] + len(src.split("\n")[ligne - 1].encode("utf-8")[:col].decode("utf-8"))


def _expression(src, etapes, connus):
    """Remplace la dernière expression `a if C else b` de main() dont C est connu par a ou b (texte conservé)."""
    arbre = ast.parse(src)
    fn = next(n for n in arbre.body if isinstance(n, ast.FunctionDef) and n.name == "main")
    trouves = [n for n in ast.walk(fn) if isinstance(n, ast.IfExp) and valeur(n.test, etapes, connus) is not None]
    if not trouves:
        return None
    n = max(trouves, key=lambda x: (x.lineno, x.col_offset))
    garde = n.body if valeur(n.test, etapes, connus) else n.orelse
    offsets, pos = [], 0
    for l in src.split("\n"):
        offsets.append(pos)
        pos += len(l) + 1
    debut = _decaler(src, offsets, n.lineno, n.col_offset)
    fin = _decaler(src, offsets, n.end_lineno, n.end_col_offset)
    texte = ast.get_source_segment(src, garde)
    if isinstance(garde, (ast.IfExp, ast.BoolOp, ast.Compare, ast.BinOp)):
        texte = f"({texte})"
    return src[:debut] + texte + src[fin:]


def deduits(src, etapes, connus):
    """Drapeaux affectés une seule fois dans main() (`x = C` ou `x = bool(C)`) dont C est connu : ajoutés à connus."""
    connus = dict(connus)
    fn = next(n for n in ast.parse(src).body if isinstance(n, ast.FunctionDef) and n.name == "main")
    while True:
        affectations = {}
        for n in ast.walk(fn):
            if isinstance(n, (ast.Assign, ast.AugAssign, ast.AnnAssign)):
                cibles = n.targets if isinstance(n, ast.Assign) else [n.target]
                for c in cibles:
                    for x in ast.walk(c):
                        if isinstance(x, ast.Name):
                            affectations.setdefault(x.id, []).append(n)
        ajout = False
        for nom, liste in affectations.items():
            if nom in connus or len(liste) != 1 or not isinstance(liste[0], ast.Assign) or len(liste[0].targets) != 1:
                continue
            v = liste[0].value
            if isinstance(v, ast.Dict) and not v.keys and not any(
                    isinstance(x, ast.Attribute) and isinstance(x.value, ast.Name) and x.value.id == nom
                    and x.attr != "get" for x in ast.walk(fn)):
                connus[nom], ajout = {}, True
                continue
            if isinstance(v, ast.Call) and getattr(v.func, "id", "") == "bool" and len(v.args) == 1:
                v = v.args[0]
            r = valeur(v, etapes, connus)
            if r is not None:
                connus[nom], ajout = r, True
        if not ajout:
            return connus


def elaguer(src, etapes, connus):
    initiaux = connus
    while True:
        avant = src
        connus = deduits(src, etapes, initiaux)
        while True:
            suivant = _expression(src, etapes, connus)
            if suivant is None:
                break
            ast.parse(suivant)
            src = suivant
        connus = deduits(src, etapes, initiaux)
        while True:
            suivant = _etape(src, etapes, connus)
            if suivant is None:
                break
            ast.parse(suivant)
            src = suivant
        if src == avant:
            return src
