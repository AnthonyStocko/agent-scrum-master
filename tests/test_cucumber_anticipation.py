"""Analyse statique d'anticipation, point 1.a : Studio à la version testée (Studio, fork du client, Standard)."""
from tools import cucumber_anticipation as an

NS = 'xmlns="https://kis.kiscloud.io/xsd/kis-engine-definition/3"'

CALLER = f"""<process-definition {NS}>
  <variable name="wave" type="X" scope="in"/>
  <process-ref key="a-sis/wms-preparation-process-exctowcs-preparationrequest" id="send">
    <mappings>
      <input to="wmsPreparationWave">$super.wave</input>
      <input to="wmsEnumWcsTriggerPreparationRequest">X.CREATE()</input>
    </mappings>
    <transitions default="estimate"/>
  </process-ref>
  <action-ref key="wms-preparation-action-estimate-launchingcampaign" id="estimate">
    <mappings>
      <input to="refProductId">$super.p</input>
      <input to="specPreparationOrderTypeToCheckReservationCode">Optional.empty()</input>
    </mappings>
    <transitions default="$end"/>
  </action-ref>
</process-definition>"""

STD_PROCESS_OLD = f"""<process-definition {NS}>
  <variable name="wmsPreparationWave" type="X" scope="in"/>
  <variable name="results" type="X" scope="out"/>
</process-definition>"""

STD_ACTION = """package com.asis.wms.preparation.action.estimate.launchingcampaign;
@Action
public class EstimateLaunchingCampaignAction {
    @In
    UUID refProductId;
    @InOut
    WmsLaunchingCampaign wmsLaunchingCampaign;
    @Inject
    Service service;
}"""

FORK_ACTION = STD_ACTION.replace("    @InOut", "    @In(optional = true)\n    Optional<WmsPreparationOrder.Type> "
                                               "specPreparationOrderTypeToCheckReservationCode;\n    @InOut")

PROCESS_PATH = ("wms-preparation-process/src/main/resources/com/asis/wms/preparation/process/exctowcs/"
                "preparationrequest/exctowcs-preparationrequest-process.xml")
ACTION_PATH = ("wms-preparation-action/src/main/java/com/asis/wms/preparation/action/estimate/launchingcampaign/"
               "EstimateLaunchingCampaignAction.java")


class FakeStd:
    def __init__(self, files):
        self.data = files      # {(dépôt, chemin): texte}

    def repos(self):
        return sorted({repo for repo, _ in self.data} | {"wms-preparation-backend", "wms-preparation-workflow",
                                                         "util-ui"})

    def ref(self, repo, candidates):
        return "c0ffee" if candidates else ""

    def ref_name(self, repo, ref):
        return "v26.22"

    def files(self, repo, ref):
        return [path for r, path in self.data if r == repo]

    def show(self, repo, ref, path):
        return self.data[(repo, path)]


class FakeClient:
    def __init__(self, files):
        self.data = files

    def definition(self, key):
        kind = an.split_key(key)[1]
        for path in an.find_definition(list(self.data), key):
            inputs = an.read_inputs(kind, self.data[path])
            if inputs is not None:
                return inputs, f"fork/{path}"
        return None, ""


def components(xml=CALLER, status="VALID"):
    return [{"nom": "wms-preparation-process-create-preparationwave", "version": 3, "statut": status, "xml": xml}]


def resolver(forks=None, client=None, std_files=None, comps=None):
    std = FakeStd(std_files if std_files is not None else {("wms-preparation-workflow", PROCESS_PATH): STD_PROCESS_OLD,
                                                           ("wms-preparation-backend", ACTION_PATH): STD_ACTION})
    custom = {c["nom"]: an.parse_process(c["xml"]) for c in (comps or components())}
    return an.Resolver(custom, forks or {}, client, std, ("origin/v26.22",), "26.22")


def test_undeclared_inputs_are_found_at_the_tested_version_without_a_run():
    result = an.check_studio(components(), resolver())
    found = {a["anomalie"].split(" passée")[0]: a for a in result["anomalies"]}
    assert set(found) == {"variable(s) wmsEnumWcsTriggerPreparationRequest",
                          "variable(s) specPreparationOrderTypeToCheckReservationCode"}
    wcs = found["variable(s) wmsEnumWcsTriggerPreparationRequest"]
    assert wcs["classe"] == an.PRODUCT and "Unable to find variable named wmsEnumWcsTriggerPreparationRequest" in \
        wcs["impact"] and wcs["ligne"] == 3 and "v26.22" in wcs["preuve"]
    assert wcs["cible"] == "wms-preparation-process-exctowcs-preparationrequest"
    assert result["controles"] == 2


def test_the_client_fork_listed_in_its_bundle_is_the_tested_version():
    # Le client déploie son fork de l'action (bundle.list) : il déclare la variable, aucune anomalie pour elle
    fork = FakeClient({"wms-preparation-backend/" + ACTION_PATH: FORK_ACTION})
    result = an.check_studio(components(), resolver({"wms-preparation-action-estimate-launchingcampaign": "1.5"},
                                                    fork))
    assert [a["cible"] for a in result["anomalies"]] == ["wms-preparation-process-exctowcs-preparationrequest"]
    # Le dépôt du client contient aussi le Standard forké en entier : seuls les composants du bundle.list y sont lus
    whole_fork = FakeClient({"wms-preparation-workflow/" + PROCESS_PATH: STD_PROCESS_OLD.replace(
        '<variable name="results"', '<variable name="wmsEnumWcsTriggerPreparationRequest" type="X" scope="in"/>'
                                    '<variable name="results"')})
    result = an.check_studio(components(), resolver({}, whole_fork))
    assert "wms-preparation-process-exctowcs-preparationrequest" in [a["cible"] for a in result["anomalies"]]


def test_bundle_list_gives_the_standard_version_and_the_client_forks():
    bundle = ("import:a-sis/wms-contrib-odatio@26.22-SNAPSHOT\n### import:a-sis/tms-contrib-odatio@26.20-SNAPSHOT\n"
              "runtime:clientx/wms-preparation-action-estimate-launchingcampaign@1.3-SNAPSHOT\n"
              "### runtime:clientx/commente@1.3\nruntime:clientx/spec-wms-preparation-action-x@1.3-SNAPSHOT\n")
    versions = an.bundle_versions([bundle])
    assert versions["standard"] == "26.22" and versions["groupe"] == "clientx"
    assert set(versions["forks"]) == {"wms-preparation-action-estimate-launchingcampaign",
                                      "spec-wms-preparation-action-x"}


def test_missing_component_structure_and_status_are_reported():
    xml = CALLER.replace('transitions default="estimate"', 'transitions default="inconnu"').replace(
        "wms-preparation-action-estimate-launchingcampaign", "wms-preparation-action-disparue")
    comps = components(xml, "DEPENDENCY_MISSING")
    result = an.check_studio(comps, resolver(comps=comps))
    texts = " | ".join(a["anomalie"] for a in result["anomalies"])
    assert "composant en DEPENDENCY_MISSING" in texts
    assert "vers « inconnu », nœud inexistant" in texts
    assert "« wms-preparation-action-disparue » absent de la version testée" in texts
    absent = next(a for a in result["anomalies"] if "absent" in a["anomalie"])
    assert absent["classe"] == an.ENVIRONMENT
    comps = components("<process-definition")
    broken = an.check_studio(comps, resolver(comps=comps))
    assert broken["anomalies"][0]["anomalie"].startswith("XML mal formé")


def test_unreadable_sources_are_never_reported_as_anomalies():
    comps = components(CALLER.replace("wms-preparation-action-estimate-launchingcampaign", "util-action-nooperation"))
    result = an.check_studio(comps, resolver(comps=comps))
    assert "util-action-nooperation" in result["non_verifies"]
    assert "util-backend" in result["non_verifies"]["util-action-nooperation"]
    # Version du Standard inconnue : rien n'est déclaré absent
    custom = {c["nom"]: an.parse_process(c["xml"]) for c in components()}
    unknown = an.Resolver(custom, {}, None, FakeStd({}), (), "")
    result = an.check_studio(components(), unknown)
    assert result["anomalies"] == [] and len(result["non_verifies"]) == 2


def test_java_inputs_and_features_at_risk():
    assert an.java_inputs(FORK_ACTION) == {"refProductId", "specPreparationOrderTypeToCheckReservationCode",
                                           "wmsLaunchingCampaign"}
    anomalies = an.check_studio(components(), resolver())["anomalies"]
    specs = [{"id": "sp-studio", "fichiers": [{"path": "studio/wms-preparation-process-create-preparationwave/"
                                                       "customized.xml"}]},
             {"id": "sp-action", "fichiers": [{"path": "x/src/main/java/com/asis/wms/preparation/action/estimate/"
                                                       "launchingcampaign/Estimate.java"}]},
             {"id": "sp-autre", "fichiers": [{"path": "studio/autre/customized.xml"}]}]
    risky = an.features_at_risk(specs, anomalies)
    assert set(risky) == {"sp-studio", "sp-action"} and len(risky["sp-studio"]) == 2
    first = an.fingerprint(components(), {"app": "a1"}, "origin/v1.3")
    assert first == an.fingerprint(components(), {"app": "a1"}, "origin/v1.3")
    assert first != an.fingerprint(components(), {"app": "a2"}, "origin/v1.3")
