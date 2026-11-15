"""Analyse statique d'anticipation, point 1.b : code du client à la version testée."""
from tools import cucumber_anticipation_code as cc

STD_CLASSES = {
    "com.asis.wms.preparation.datamodel.WmsPreparationOrder": {
        "super": "com.asis.kis.Base", "interfaces": ["com.asis.wms.preparation.datamodel.HasCode"],
        "methodes": {"getCode": [[0, False, False]], "setStatus": [[1, False, False]]}},
    "com.asis.wms.preparation.datamodel.HasCode": {"super": "java.lang.Object", "interfaces": [],
                                                   "methodes": {"codeOrEmpty": [[0, False, False]]}},
    "com.asis.kis.Base": {"super": "java.lang.Object", "interfaces": [], "methodes": {"getId": [[0, False, False]]}},
    "com.asis.wms.preparation.datamodel.Query": {"super": "java.lang.Object", "interfaces": [],
                                                 "methodes": {"getAisle": [[0, False, False]]}},
    "com.asis.wms.preparation.datamodel.Query$Inner": {"super": "java.lang.Object", "interfaces": [],
                                                       "methodes": {}},
}


def api():
    std = cc.StandardApi("a-sis", "26.24-SNAPSHOT", fetch=lambda path: b"")
    std.classes.update(STD_CLASSES)
    std.artifacts["wms-preparation-datamodel"] = "jar"
    std.artifacts["kis"] = ""
    return std


def test_calls_to_the_standard_are_checked_against_its_jars_at_the_version():
    code = """package canson.spec;
import com.asis.wms.preparation.datamodel.WmsPreparationOrder;
import com.asis.wms.preparation.datamodel.Disparue;
import com.asis.wms.preparation.datamodel.Query;
import canson.spec.Own;
class A {
    WmsPreparationOrder order;
    void f(Query query) {
        order.getCode(); order.codeOrEmpty(); order.getId();
        order.setStatus(1, 2);
        order.renamed();
        Query.Inner inner = new Query.Inner();
    }
    void g() { Query query = new Query(); }
}"""
    found, unverified = cc.check_api(code, "A.java", api(), {"canson.spec.Own"}, "com.asis.")
    texts = [a["anomalie"] for a in found]
    assert any("Disparue absente du Standard 26.24-SNAPSHOT" in t for t in texts)
    assert any("setStatus : 2 argument(s)" in t for t in texts)
    assert any("WmsPreparationOrder.renamed() n'existe pas" in t for t in texts)
    # Interface et classe mère lues : codeOrEmpty / getId existent ; constructeur de classe imbriquée ignoré
    assert not any("codeOrEmpty" in t or "getId" in t or "Inner" in t for t in texts)
    assert len(found) == 3


def test_unreadable_hierarchy_and_ambiguous_variables_are_never_reported():
    std = api()
    std.classes["com.asis.wms.preparation.datamodel.WmsPreparationOrder"]["super"] = "org.hibernate.Entity"
    code = """import com.asis.wms.preparation.datamodel.WmsPreparationOrder;
import com.asis.wms.preparation.datamodel.Query;
class A { WmsPreparationOrder order; void f() { order.inherited(); }
  void g(Query query) { query.getAisle(); }
  class Local { Local query; void h() { query.ignoreInitialLocation(true); } } }"""
    found, _ = cc.check_api(code, "A.java", std, set(), "com.asis.")
    assert found == []


def test_typed_variables_drop_variables_declared_with_several_types():
    typed = cc.typed_variables("LocationPerimeter.Query query; Query query = new Query(); WmsX x; "
                               "void f(com.a.b.Pojo p){} Pojo p;")
    assert typed == {"x": "WmsX"}


class FakeDb:
    def __call__(self, sql, schema):
        if "information_schema.tables" in sql:
            return [{"code": "wms_enum_cubing_type"}]
        if 'FROM "wms_enum_cubing_type"' in sql:
            return [{"code": "KIT_MONO"}, {"code": "STANDARD"}]
        if "kis_custom_field" in sql:
            return [{"code": 'com.asis.wms.preparation.datamodel.WmsPreparationOrder@@[{"name":"customString1",'
                             '"alias":"Imprimante_BL"}]'}]
        return []


def test_enums_custom_fields_and_translations_must_be_declared():
    declarations = cc.Declarations("s", FakeDb())
    java = """import com.asis.wms.preparation.datamodel.WmsEnumCubingType;
import com.asis.wms.preparation.datamodel.WmsPreparationOrder;
class A { WmsEnumCubingType.Enums wmsEnumCubingType; WmsPreparationOrder order;
  void f() { wmsEnumCubingType.KIT_MONO(); wmsEnumCubingType.KIT_MULTI(); order.getCustomString1();
             order.setCustomString2("x"); } }"""
    enums = cc.check_enums_java(java, "A.java", declarations)
    assert [a["anomalie"] for a in enums] == ["valeur KIT_MULTI de l'énumération WmsEnumCubingType non déclarée"]
    fields = cc.check_custom_fields_java(java, "A.java", declarations)
    assert [a["anomalie"] for a in fields] == ["champ personnalisé customString2 de WmsPreparationOrder non déclaré"]
    # Recopie générique de tous les champs (mappage) : ignorée
    generic = java.replace('order.setCustomString2("x");', " ".join(
        f'order.setCustomString{i}("x");' for i in range(2, 8)))
    assert cc.check_custom_fields_java(generic, "A.java", declarations) == []
    xml = """<process-definition xmlns="https://kis.kiscloud.io/xsd/kis-engine-definition/3">
  <variable name="wmsPreparationOrder" type="com.asis.wms.preparation.datamodel.WmsPreparationOrder" scope="in"/>
  <action-ref key="a-sis/util-action-notify-message" id="n">
    <mappings>
      <input to="message">$translate.message("Delivery note edition on {0}")</input>
      <input to="other">$translate.message("Ancienne cle")</input>
      <input to="type">$WmsEnumCubingType.KIT_DUO()</input>
      <input to="v">$super.wmsPreparationOrder.customString3</input>
    </mappings>
    <transitions default="$end"/>
  </action-ref>
</process-definition>"""
    components = [{"nom": "spec-p", "version": 1, "statut": "VALID", "xml": xml}]
    found = cc.check_studio_texts(components, {"spec-p": '$translate.message("Ancienne cle")'},
                                  lambda name: "OK:OK\n", declarations)
    texts = sorted(a["anomalie"] for a in found)
    assert texts == ["champ personnalisé customString3 de WmsPreparationOrder non déclaré",
                     "clé de traduction « Delivery note edition on {0} » sans traduction française",
                     "valeur KIT_DUO de l'énumération WmsEnumCubingType non déclarée"]


def test_log_level_conditioned_processing_is_found():
    code = '''class A { void f() {
 if (logger.isDebugEnabled()) { refs.forEach(r -> logger.debug("x", r)); }
 if (logger.isInfoEnabled()) { int maxLength; if (x) { maxLength = Math.min(1, 2); logger.info("a", maxLength); } }
 if (logger.isDebugEnabled()) { fam.forEach((k, v) -> { if (logger.isDebugEnabled()) { logger.debug("k", k); } }); }
 if (logger.isDebugEnabled()) {
    String code = null;
    if (y) { code = draft.get(); }
    logger.debug("c", code); }
 } else if (logger.isDebugEnabled()) { logger.debug("x"); removeCurrent(); manageNextOrEnd(); }
}}'''
    found = cc.check_log_level(code, "A.java")
    assert len(found) == 1 and "removeCurrent()" in found[0]["anomalie"] and found[0]["ligne"] == 9


def test_hardcoded_external_hosts_are_found_but_not_namespaces():
    java = ('String u = "http://10.8.140.35:8091/x"; String v = "https://www.w3.org/2001/XMLSchema"; '
            'String h = "192.168.1.2"; String ver = "1.2.3.4.5"; // "http://commente:80"\n'
            'String p = "http://precubing:80";')
    found = cc.check_hardcoded_hosts(java, "A.java")
    assert sorted(a["cible"] for a in found) == ["10.8.140.35", "192.168.1.2", "precubing"]
    xml = '<beans xmlns="http://www.springframework.org/schema/beans"><url>https://api.transporteur.fr/v1</url></beans>'
    assert [a["cible"] for a in cc.check_hardcoded_hosts(xml, "src/main/resources/a.xml")] == ["api.transporteur.fr"]


def test_deployed_sources_are_the_bundle_modules_only():
    class Client:
        repos = {"app": "/app", "app-cucumber": "/c"}
        data = {"m1/pom.xml": "<project><parent><artifactId>p</artifactId></parent><artifactId>mod-a</artifactId>"
                              "</project>",
                "m1/src/main/java/a/A.java": "class A {}",
                "m2/pom.xml": "<project><artifactId>mod-b</artifactId></project>",
                "m2/src/main/java/b/B.java": "class B {}"}

        def files(self, name):
            return list(self.data) if name == "app" else []

        def show(self, name, path):
            return self.data[path]
    sources = cc.deployed_sources(Client(), ["mod-a"])
    assert sources == [("app", "m1/src/main/java/a/A.java")]
    assert cc.client_classes(sources) == {"a.A"}
    assert cc.pom_artifact(Client.data["m1/pom.xml"]) == "mod-a"
