"""Analyse statique d'anticipation, point 1.c : configuration de la copie de référence."""
import re

from tools import cucumber_anticipation_config as ac


def java_map(pairs: list) -> bytes:
    """JobDataMap sérialisé simplifié : en-tête binaire, puis clé / valeur en TC_STRING (0x74), valeur nulle 0x70."""
    blob = b"\xac\xed\x00\x05sr\x00\x15org.quartz.JobDataMap\x9f\xb0\x83\xe8\xbf\xa9\xb0\xcb\x02\x00\x00xp"
    for key, value in pairs:
        blob += b"t" + len(key).to_bytes(2, "big") + key.encode()
        blob += b"p" if value is None else (b"t" + len(value.encode()).to_bytes(2, "big") + value.encode())
    return blob + b"x"


class FakeDb:
    """Base de test : colonnes, clés étrangères et contenu des tables, requêtes SQL interprétées sommairement."""
    columns = [("exc_message_partner", "code", "character varying"), ("exc_message_partner", "start_url", "text"),
               ("wms_preparation_saas_param", "code", "character varying"),
               ("wms_preparation_saas_param", "url", "character varying"),
               ("wms_printer", "printer_host", "character varying"),
               ("wms_plant_config", "wms_preparation_order_type", "character varying"),
               ("wms_plant_location", "wms_preparation_order_type", "character varying"),
               ("wms_preparation_order_type", "id", "character varying"),
               ("ref_packaging", "code", "character varying"), ("ref_consumer", "code", "character varying"),
               ("kis_menu_item", "item", "text"), ("exc_message_histo", "url", "text"),
               ("kis_profile_config", "config", "text")]
    foreign = [("wms_plant_location", "wms_preparation_order_type")]
    tables = {
        ("exc_message_partner", "start_url"): [("LM Connect", "https://user:secret@10.8.140.35:8091/lm?token=x")],
        ("wms_preparation_saas_param", "url"): [("CUBING", "http://precubing:80"), ("LOCAL", "http://localhost:8080")],
        ("wms_printer", "printer_host"): [("", "\\\\imprimante-quai\\etiquettes")],
        ("wms_plant_config", "wms_preparation_order_type"): [("", "NA"), ("", "DISPARU")],
        ("wms_plant_location", "wms_preparation_order_type"): [("", "AUTRE")],
        ("kis_menu_item", "item"): [("", '{"processKeyName":"client/spec-wms-preparation-process-pairing",'
                                         '"autre":"a-sis/wms-preparation-process-retire",'
                                         '"inconnu":"a-sis/wms-content-process-indetermine"}')],
        ("exc_message_histo", "url"): [("", "http://historique:80")],
        # Code d'un consommateur nommé comme un processus (dupliqué sous n'importe quel code) : un identifiant
        ("ref_consumer", "code"): [("", "a-sis/ref-activityreport-process-send-gs1-retire-to-lmconnect")],
        ("kis_profile_config", "config"): [("", '{"consumer":"a-sis/ref-activityreport-process-send-gs1-retire-'
                                                 'to-lmconnect"}')],
    }
    codes = {"wms_preparation_order_type": {"NA", "TRANSFER"}, "ref_packaging": {"BAC PM"},
             "ref_consumer": {"a-sis/ref-activityreport-process-send-gs1-toany"}}
    jobs = [("GS1", java_map([("refConsumerCode", "a-sis/ref-activityreport-process-send-gs1-toany"),
                              ("description", "Image"), ("jobType", "com.asis.wms.Existing")])),
            ("Expédition", java_map([("refConsumerCode", "disparu"), ("refSiteCode", None),
                                     ("jobType", "com.asis.wms.Removed")]))]
    parameters = [("TRANSPORT_MANAGEMENT", "com.asis.wms.preparation.datamodel.WmsEnumTransportManagement", "MASTERX"),
                  ("NO_PACKAGING_PTS_PACKAGING_LIST", "", "BAC PM, BAC GM")]

    def __call__(self, sql, schema):
        if "information_schema.columns" in sql:
            return [{"code": f"{t}@@{c}@@{k}"} for t, c, k in self.columns]
        if "pg_constraint" in sql:
            return [{"code": f"{t}@@{c}"} for t, c in self.foreign]
        if "kis_qrtz_job_details" in sql:
            return [{"code": f"{name}@@{blob.hex()}"} for name, blob in self.jobs]
        if "FROM ref_parameter" in sql:
            return [{"code": f"{n}@@{e}@@{v}"} for n, e, v in self.parameters]
        match = re.match(r'SELECT code::text AS code FROM "(\w+)" WHERE code::text IN \((.*)\)$', sql)
        if match:
            wanted = set(re.findall(r"'([^']*)'", match.group(2)))
            return [{"code": v} for v in sorted(self.codes.get(match.group(1), set()) & wanted)]
        out = []
        for part in re.findall(r"\(SELECT .*? LIMIT \d+\)", sql):
            table, column = re.search(r"'(\w+)@@(\w+)@@'", part).groups()
            rows = self.tables.get((table, column), [])
            if "~*" in part:            # adresses
                out += [{"code": f"{table}@@{column}@@{code}@@{v}"} for code, v in rows
                        if re.search(r"(://|^[a-z0-9][a-z0-9.-]*:[0-9]{2,5}(/|$)|^\\\\)", v, re.I)]
            elif "NOT IN" in part:      # énumérations
                out += [{"code": f"{table}@@{column}@@{v}"} for _c, v in rows if v not in self.codes.get(column, set())]
            elif "regexp_matches" in part:
                out += [{"code": f"{table}@@{column}@@{k}"} for _c, v in rows
                        for k in re.findall(r"[a-z][a-z0-9-]*/[a-z][a-z0-9-]*-(?:process|action)-[a-z0-9-]*[a-z0-9]", v)]
        return out


class FakeDeclarations:
    def enum_values(self, enum_class):
        return {"MASTER", "SLAVE"} if enum_class.endswith("WmsEnumTransportManagement") else None


def components(key):
    if key.endswith("retire"):
        return "absent", "absent des dépôts wms-preparation-workflow du Standard 26.24"
    if key.startswith("client/"):
        return "ok", "processus Studio déployé"
    return "non_verifie", "dépôt absent"


def test_external_connections_keep_the_host_only_and_skip_history_and_localhost():
    found = ac.external_connections(ac.Schema("s", FakeDb()))
    assert sorted((c["categorie"], c["code"], c["hote"]) for c in found) == [
        ("imprimante", "", "imprimante-quai"), ("partenaire de messages", "LM Connect", "10.8.140.35"),
        ("précolisage", "CUBING", "precubing")]
    assert "secret" not in str(found) and "token" not in str(found)
    rows = ac.check_external(found)
    assert all(a["classe"] == ac.ENVIRONMENT for a in rows)
    assert any("INIT- Configuration du colisage" in a["action"] for a in rows if a["fichier"].startswith("wms_prep"))


def test_parameters_jobs_and_references_pointing_to_absent_objects():
    result = ac.analyse_config("s", FakeDb(), FakeDeclarations(),
                               class_exists=lambda fqn: fqn != "com.asis.wms.Removed", component_exists=components)
    texts = sorted(a["anomalie"] for a in result["anomalies"] if not a["anomalie"].startswith("branchement"))
    assert texts == [
        "classe com.asis.wms.Removed de la tâche planifiée « Expédition » introuvable",
        "composant a-sis/wms-preparation-process-retire référencé par kis_menu_item.item absent de la version testée",
        "paramètre NO_PACKAGING_PTS_PACKAGING_LIST : code(s) BAC GM absent(s) de ref_packaging",
        "paramètre TRANSPORT_MANAGEMENT = « MASTERX » absent de l'énumération WmsEnumTransportManagement",
        "refConsumerCode « disparu » de la tâche planifiée « Expédition » absent de ref_consumer",
        "valeur « DISPARU » de wms_plant_config.wms_preparation_order_type absente de l'énumération "
        "wms_preparation_order_type"]
    # Colonne sous clé étrangère : la base la garantit, non relue ; référence Studio résolue : pas d'anomalie
    assert not any("AUTRE" in a["anomalie"] or "client/" in a["anomalie"] for a in result["anomalies"])
    # Composant ni prouvé présent ni prouvé absent : non vérifié, jamais une anomalie
    assert "a-sis/wms-content-process-indetermine" in result["non_verifies"]
    # Codes d'objets nommés comme des composants (consommateurs, refConsumerCode des tâches) : jamais contrôlés
    assert not any("lmconnect" in a["anomalie"] for a in result["anomalies"])
    assert not any("toany" in k or "lmconnect" in k for k in result["non_verifies"])


def test_job_values_pair_each_key_with_the_string_that_follows_it():
    values = ac.job_values(java_map([("refSiteCode", None), ("description", "Expédition auto"),
                                     ("jobType", "com.asis.X")]))
    assert values == {"description": "Expédition auto", "jobType": "com.asis.X"}


def test_unreadable_copy_is_never_reported_as_clean():
    def broken(sql, schema):
        raise RuntimeError("connexion refusée")
    result = ac.analyse_config("s", broken)
    assert result["anomalies"] == [] and "configuration" in result["non_verifies"]
