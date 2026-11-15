"""
Sélection déterministe des données de test (0 token), avant d'écrire ou de corriger un test.

Lue dans le schéma des tests (copie restaurée en configuration copiée, état après init sinon), en lecture seule :
- réserves VIDES : mode NA, disponibilité INOUT, emplacement libre (ref_location.status FREE), ni contenu, ni UM, ni
  implantation, ni verrou, ni blocage ; dans le secteur de stockage des emplacements déjà cités par le test s'il y en
  a (secteur cohérent), sinon dans le secteur le mieux fourni ;
- emplacements de picking fixe LIBRES (à implanter par le test pour son propre produit) ;
- préfixe GS1 du site et codes d'UM conformes (18 chiffres, clé GS1), absents de la base ;
- donneur d'ordres et statuts administratifs existants (configuration) ;
- couple transporteur / mode d'expédition le plus utilisé par les OP du client passées sans erreur de transport : sans
  lui, une commande (WOI) est rejetée à l'affectation du transport (TRANSPORT_ERROR,
  CARRIER_DISPATCH_MODE_NOT_COHERENT, build Canson 415) et n'est jamais « A lancer ».
Un emplacement déjà cité par un autre test du client n'est pas proposé. Les valeurs et leur preuve (requête,
résultat) sont injectées dans le prompt : l'agent ne les choisit plus lui-même.

Configuration copiée : une réserve occupée dans la copie (contenu antérieur à la copie) est refusée au contrôle des
fichiers (mélange de produits interdit par les contraintes de l'emplacement).
"""
import os
import re
import time
from datetime import datetime

from tools.cucumber_lessons import _feature_tables

RESERVES = 6
PICKINGS = 3
UM_CODES = 4
LOCATION_COLUMNS = ("wmslocationcode", "wmslocation.reflocation.code", "locationcode", "emplacement")
SEP = "@@"
_CODE = re.compile(r"^[\w.\-/ ]{1,60}$")
EMPTY_RESERVE_LESSON = "en configuration copiée, un emplacement de réserve doit être vide"
TRANSPORT_LESSON = ("une commande (WOI) porte le couple transporteur / mode d'expédition du client "
                    "(shipmentTransportationInformation : transportServiceLevelCode + carrier), sinon elle est rejetée "
                    "à l'affectation du transport (TRANSPORT_ERROR, CARRIER_DISPATCH_MODE_NOT_COHERENT)")
WOI_MARK = "warehousingOutboundInstruction"
TRANSPORT_SQL = (
    "SELECT c.code || '@@' || dm.code || '@@' || count(*) AS code FROM wms_preparation_order po "
    "JOIN ref_carrier c ON c.ref_carrier_id = po.ref_carrier_id "
    "JOIN ref_dispatch_mode dm ON dm.ref_dispatch_mode_id = po.ref_dispatch_mode_id "
    "WHERE po.status NOT IN ('TRANSPORT_ERROR', 'CUBING_ERROR', 'CANCELLED') "
    "AND coalesce(po.rejection_status, 'NONE') = 'NONE' GROUP BY c.code, dm.code ORDER BY count(*) DESC LIMIT 3")

_LOCATION_JOIN = (
    "FROM wms_location l JOIN ref_location rl ON rl.ref_location_id = l.ref_location_id "
    "JOIN ref_site s ON s.ref_site_id = rl.ref_site_id "
    "LEFT JOIN wms_sector_action sas ON sas.wms_sector_action_id = l.wms_sector_action_storage_id "
    "LEFT JOIN wms_sector ss ON ss.wms_sector_id = sas.wms_sector_id "
    "LEFT JOIN wms_sector_action sap ON sap.wms_sector_action_id = l.wms_sector_action_prep_id "
    "LEFT JOIN wms_sector ps ON ps.wms_sector_id = sap.wms_sector_id ")
_FREE = (
    "rl.status = 'FREE' AND l.locked_by IS NULL AND coalesce(l.awaiting_deletion, false) = false "
    "AND l.wms_blocking_reason_code IS NULL "
    "AND NOT EXISTS (SELECT 1 FROM wms_content c WHERE c.wms_location_id = l.wms_location_id) "
    "AND NOT EXISTS (SELECT 1 FROM wms_handling_unit h WHERE h.wms_location_id = l.wms_location_id) "
    "AND NOT EXISTS (SELECT 1 FROM wms_plant_location p WHERE p.wms_location_id = l.wms_location_id)")


def _quote(values) -> str:
    return ", ".join("'" + str(v).replace("'", "''") + "'" for v in values)


def _rows(query, sql: str, schema: str) -> list:
    """Lignes « a@@b@@c » de la 1re colonne découpées ; base illisible : []."""
    try:
        return [str(r.get("code", "")).split(SEP) for r in query(sql, schema) if str(r.get("code", "")).strip()]
    except Exception:  # noqa: BLE001 - table absente ou base injoignable : pas de sélection
        return []


def feature_locations(text: str) -> set:
    """Codes d'emplacement cités dans les tables d'une feature (colonnes d'emplacement)."""
    found = set()
    for _step, headers, rows, _line in _feature_tables(text or ""):
        columns = [i for i, h in enumerate(headers) if h.strip().lower() in LOCATION_COLUMNS]
        for row in rows:
            for i in columns:
                if i < len(row) and _CODE.match(row[i]) and not row[i].startswith("$"):
                    found.add(row[i])
    return found


def client_locations(worktree: str, root: str = "src/test/resources/cucumber/features") -> dict:
    """{fichier: emplacements cités} des features du dépôt (pour ne pas proposer un emplacement déjà utilisé)."""
    out = {}
    base = os.path.join(worktree or "", *root.split("/"))
    for folder, _dirs, names in os.walk(base):
        for name in names:
            if name.endswith(".feature"):
                path = os.path.join(folder, name)
                with open(path, encoding="utf-8", errors="replace") as f:
                    found = feature_locations(f.read())
                if found:
                    out[os.path.relpath(path, worktree).replace(os.sep, "/")] = found
    return out


def gs1_check_digit(digits: str) -> str:
    """Clé GS1 (modulo 10, poids 3 et 1 depuis la droite) d'une suite de chiffres."""
    total = sum(int(d) * (3 if i % 2 == 0 else 1) for i, d in enumerate(reversed(digits)))
    return str((10 - total % 10) % 10)


def um_codes(prefix: str, count: int, seed: int = None) -> list:
    """Codes d'UM de 18 chiffres : préfixe GS1 du site, numéro unique au run, clé GS1."""
    if not prefix or not prefix.isdigit() or len(prefix) >= 17:
        return []
    seed = int(seed if seed is not None else time.time()) % 10 ** (17 - len(prefix) - 2)
    out = []
    for i in range(count):
        body = (prefix + str(seed).zfill(17 - len(prefix) - 2) + str(i).zfill(2))[:17]
        out.append(body + gs1_check_digit(body))
    return out


def select(schema: str, query, worktree: str = "", current_files: list = (), site: str = "") -> dict:
    """
    Données de test choisies dans `schema` : {site, gs1, reserves, secteur, pickings, um, donneur_ordres, statuts,
    preuves: [{objet, requete, resultat}]}. Base illisible : {} (aucune valeur imposée).
    """
    proofs = []
    sites = _rows(query, "SELECT code || '@@' || coalesce(gs1prefix, '') || '@@' || coalesce(default_site::text, '') "
                         "AS code FROM ref_site ORDER BY default_site DESC NULLS LAST, code", schema)
    if not sites:
        return {}
    chosen = next((s for s in sites if s[0] == site), sites[0])
    site, gs1 = chosen[0], (chosen[1] if len(chosen) > 1 else "")
    used = client_locations(worktree) if worktree else {}
    mine = set().union(*(v for k, v in used.items() if k in set(current_files))) if used else set()
    others = set().union(*(v for k, v in used.items() if k not in set(current_files))) if used else set()
    # Secteur cohérent : celui des emplacements déjà cités par le test (hors réserves occupées)
    sector = ""
    if mine:
        rows = _rows(query, f"SELECT coalesce(ss.code, '') || '@@' || count(*) AS code {_LOCATION_JOIN}"
                            f"WHERE rl.code IN ({_quote(sorted(mine))}) GROUP BY ss.code ORDER BY count(*) DESC",
                     schema)
        sector = next((r[0] for r in rows if r[0]), "")
    reserve_where = (f"WHERE s.code = '{site}' AND l.plant_mode = 'NA' AND l.availability = 'INOUT' AND {_FREE}"
                     + (f" AND rl.code NOT IN ({_quote(sorted(others | mine))})" if others | mine else ""))
    if not sector:
        rows = _rows(query, f"SELECT coalesce(ss.code, '') || '@@' || count(*) AS code {_LOCATION_JOIN}"
                            f"{reserve_where} GROUP BY ss.code ORDER BY count(*) DESC LIMIT 1", schema)
        sector = rows[0][0] if rows else ""
    sql = (f"SELECT rl.code || '@@' || coalesce(ss.code, '') || '@@' || coalesce(ps.code, '') AS code {_LOCATION_JOIN}"
           f"{reserve_where}" + (f" AND ss.code = '{sector}'" if sector else "") + f" ORDER BY rl.code LIMIT {RESERVES}")
    reserves = [{"code": r[0], "stockage": r[1] if len(r) > 1 else "", "preparation": r[2] if len(r) > 2 else ""}
                for r in _rows(query, sql, schema)]
    proofs.append({"objet": "réserves vides", "requete": sql,
                   "resultat": ", ".join(r["code"] for r in reserves) or "aucune"})
    picking_sql = (f"SELECT rl.code || '@@' || coalesce(ps.code, '') AS code {_LOCATION_JOIN}"
                   f"WHERE s.code = '{site}' AND l.plant_mode = 'FIXED_PICKING' AND l.availability = 'INOUT' AND {_FREE}"
                   + (f" AND rl.code NOT IN ({_quote(sorted(others | mine))})" if others | mine else "")
                   + f" ORDER BY rl.code LIMIT {PICKINGS}")
    pickings = [{"code": r[0], "preparation": r[1] if len(r) > 1 else ""} for r in _rows(query, picking_sql, schema)]
    proofs.append({"objet": "picking fixe libre", "requete": picking_sql,
                   "resultat": ", ".join(p["code"] for p in pickings) or "aucun"})
    codes = um_codes(gs1, UM_CODES * 3)
    if codes:
        existing = {r[0] for r in _rows(query, f"SELECT code AS code FROM wms_handling_unit WHERE code IN "
                                               f"({_quote(codes)})", schema)}
        codes = [c for c in codes if c not in existing][:UM_CODES]
    owners = _rows(query, "SELECT so.code || '@@' || coalesce(string_agg(a.code || CASE WHEN a.standard THEN ' "
                          "(standard)' ELSE '' END, ', ' ORDER BY a.standard DESC, a.code), '') AS code "
                          "FROM ref_stock_owner so LEFT JOIN ref_admin_status a ON a.ref_stock_owner_id = "
                          "so.ref_stock_owner_id GROUP BY so.code ORDER BY so.code LIMIT 5", schema)
    proofs.append({"objet": "donneurs d'ordres et statuts administratifs",
                   "requete": "ref_stock_owner ⋈ ref_admin_status",
                   "resultat": " ; ".join(f"{o[0]} : {o[1] if len(o) > 1 else ''}" for o in owners) or "aucun"})
    transport = transport_pairs(schema, query)
    proofs.append({"objet": "couple transporteur / mode d'expédition", "requete": TRANSPORT_SQL,
                   "resultat": ", ".join(f"{t['transporteur']} / {t['mode']} ({t['op']} OP)" for t in transport)
                   or "aucun"})
    return {"site": site, "gs1": gs1, "reserves": reserves, "secteur": sector, "pickings": pickings, "um": codes,
            "transport": transport, "donneurs_ordres": [{"code": o[0], "statuts": o[1] if len(o) > 1 else ""} for o in owners],
            "preuves": proofs, "schema": schema, "date": datetime.now().isoformat(timespec="seconds")}


def transport_pairs(schema: str, query) -> list:
    """Couples transporteur / mode d'expédition des OP du client passées sans erreur de transport (plus utilisés
    d'abord) : [{transporteur, mode, op}]. Base illisible ou aucune OP : []."""
    return [{"transporteur": r[0], "mode": r[1], "op": r[2] if len(r) > 2 else ""}
            for r in _rows(query, TRANSPORT_SQL, schema) if len(r) > 1 and r[0] and r[1]]


def missing_transport(worktree: str, files: list, schema: str, query) -> list:
    """
    Erreurs des commandes (WOI) du lot sans couple transporteur / mode d'expédition alors que les OP du client en
    portent un (transport_pairs). Base illisible ou client sans couple : [] (jamais bloquant sur une panne).
    """
    woi = []
    for rel in files:
        path = os.path.join(worktree, *rel.split("/"))
        if rel.lower().endswith(".xml") and os.path.exists(path):
            with open(path, encoding="utf-8", errors="replace") as f:
                text = f.read()
            if WOI_MARK in text and ("<transportServiceLevelCode>" not in text or "<carrier>" not in text):
                woi.append(rel)
    if not woi or not schema:
        return []
    pairs = transport_pairs(schema, query)
    if not pairs:
        return []
    best = pairs[0]
    return [f"{rel} : commande sans couple transporteur / mode d'expédition — {TRANSPORT_LESSON} : ajoute après "
            f"</plannedDelivery> <shipmentTransportationInformation><transportServiceLevelCode>{best['mode']}"
            f"</transportServiceLevelCode><carrier><additionalPartyIdentification additionalPartyIdentificationTypeCode="
            f"\"FOR_INTERNAL_USE_1\">{best['transporteur']}</additionalPartyIdentification></carrier>"
            f"</shipmentTransportationInformation> (couple de {best['op']} OP du client sans erreur de transport)."
            for rel in woi]


def prompt_block(selection: dict, copied: bool = False) -> str:
    """Bloc « DONNÉES DE TEST CHOISIES » du prompt ('' sans sélection)."""
    if not selection or not selection.get("site"):
        return ""
    lines = [f"\nDONNÉES DE TEST CHOISIES (sélection automatique sans IA, lue dans le schéma {selection['schema']} le "
             f"{selection['date'][:16].replace('T', ' ')}, preuve ci-dessous) : utilise CES valeurs, une réserve "
             "différente par scénario, sans nouvelle requête pour les choisir ; ne les remplace que sur une erreur "
             "prouvée qui les vise (citée dans ta réponse)."]
    if selection["reserves"]:
        lines.append(f"- Réserves VIDES du site {selection['site']} (mode NA, INOUT, libres : ni contenu, ni UM, ni "
                     f"implantation) — secteur de stockage « {selection['secteur'] or '?'} » : "
                     + ", ".join(f"{r['code']} (préparation {r['preparation'] or '?'})" for r in selection["reserves"]))
    if selection["pickings"]:
        lines.append(f"- Picking fixe libre du site {selection['site']}, à implanter par le test pour SON produit : "
                     + ", ".join(p["code"] for p in selection["pickings"]))
    if selection["gs1"]:
        lines.append(f"- Préfixe GS1 du site {selection['site']} : {selection['gs1']} ; codes d'UM conformes (18 "
                     f"chiffres, clé GS1, absents de la base) : " + ", ".join(selection["um"]))
    for pair in selection.get("transport") or []:
        lines.append(f"- Couple transporteur / mode d'expédition du client : {pair['transporteur']} / {pair['mode']} "
                     f"({pair['op']} OP sans erreur de transport) — dans chaque commande (WOI) : "
                     "shipmentTransportationInformation après plannedDelivery (transportServiceLevelCode = mode, "
                     "carrier/additionalPartyIdentification FOR_INTERNAL_USE_1 = transporteur)")
    for owner in selection["donneurs_ordres"]:
        lines.append(f"- Donneur d'ordres {owner['code']} ; statuts administratifs existants : {owner['statuts']}")
    lines.append("- Produit : " + ("créé par le test lui-même (jamais un produit de la copie, donnée métier)"
                                   if copied else "créé par le test ou existant après l'init, jamais inventé"))
    if copied:
        lines.append(f"- Règle : {EMPTY_RESERVE_LESSON} (une réserve occupée dans la copie refuse le stock : mélange "
                     f"de produits interdit par ses contraintes).")
    lines.append("Preuves : " + " | ".join(f"{p['objet']} → {p['resultat']}" for p in selection["preuves"]))
    return "\n".join(lines) + "\n"


def occupied_reserves(worktree: str, files: list, schema: str, reference_date: str, query) -> list:
    """
    Configuration copiée : erreurs des features du lot qui créent du stock sur une réserve OCCUPÉE dans la copie
    (contenu antérieur à la copie). Base illisible : [] (jamais bloquant sur une panne).
    """
    cited = {}
    for rel in files:
        path = os.path.join(worktree, *rel.split("/"))
        if rel.endswith(".feature") and os.path.exists(path):
            with open(path, encoding="utf-8", errors="replace") as f:
                for code in feature_locations(f.read()):
                    cited.setdefault(code, rel)
    if not cited or not schema:
        return []
    date_filter = f" AND c.created_date <= '{reference_date[:19]}'" if reference_date else ""
    rows = _rows(query, f"SELECT DISTINCT rl.code AS code FROM wms_location l JOIN ref_location rl ON "
                        f"rl.ref_location_id = l.ref_location_id JOIN wms_content c ON c.wms_location_id = "
                        f"l.wms_location_id WHERE l.plant_mode = 'NA' AND rl.code IN ({_quote(sorted(cited))})"
                        f"{date_filter}", schema)
    return [f"{cited[r[0]]} : réserve « {r[0]} » déjà occupée dans la copie du client — {EMPTY_RESERVE_LESSON} "
            f"(mélange de produits refusé) : prends une réserve du bloc « DONNÉES DE TEST CHOISIES »."
            for r in rows if r and r[0] in cited]
