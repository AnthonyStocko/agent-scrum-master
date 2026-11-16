package cucumber.nettoyage;

import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.ObjectMapper;
import cucumber.endPoints.refReport.RefReportDocEndPoints;
import cucumber.pojo.request.refReportDocDraftRequest.Properties;
import cucumber.pojo.request.refReportDocDraftRequest.RefReportDocDraftRequest;
import cucumber.pojo.request.refReportDocDraftRequest.State;
import cucumber.pojo.response.refReport.refReportDoc.RefReportDoc;
import cucumber.pojo.response.refReportDocDraftResponse.RefReportDocDraftResponse;
import cucumber.util.ManageFileUtil;
import cucumber.util.context.Context;
import cucumber.util.context.ScenarioContext;
import cucumber.util.context.TestContext;
import io.cucumber.datatable.DataTable;
import io.cucumber.java.After;
import io.cucumber.java.AfterStep;
import io.cucumber.java.Before;
import io.cucumber.java.BeforeStep;
import io.cucumber.java.Scenario;
import org.w3c.dom.Document;
import org.w3c.dom.NodeList;

import javax.xml.parsers.DocumentBuilderFactory;
import javax.xml.xpath.XPath;
import javax.xml.xpath.XPathConstants;
import javax.xml.xpath.XPathFactory;
import java.io.File;
import java.io.InputStream;
import java.lang.reflect.InvocationTargetException;
import java.lang.reflect.Method;
import java.util.ArrayList;
import java.util.Collections;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import java.util.regex.Matcher;
import java.util.regex.Pattern;

/**
 * Journal des données d'un scénario et nettoyage en fin de scénario (réussi ou non), pour que le test laisse
 * l'environnement dans l'état où il l'a trouvé, même exécuté sans l'agent (Jenkins, poste d'un développeur).
 * <p>
 * Chaque step de mise en place connu du catalogue (cucumber/nettoyage/catalogue.json) est journalisé : ce qu'il crée
 * (lignes de sa table, valeurs des fichiers intégrés) et ce qu'il modifie (valeur d'origine LUE avant). Le hook
 * {@code @After} rejoue, dans l'ordre inverse, les steps de suppression, d'annulation ou de clôture du Standard, et
 * remet les valeurs d'origine. Tolérant : un objet déjà absent n'est pas une erreur, un échec est journalisé
 * ([NETTOYAGE]) sans masquer le résultat du scénario. Installé par l'agent ; ne modifier que dans l'agent.
 */
public class NettoyageHooks {

    private static final String CATALOGUE = "cucumber/nettoyage/catalogue.json";
    private static final String CATALOGUE_PROJET = "cucumber/nettoyage/catalogue_projet.json";
    private static final int MAX_REPETITIONS = 20;
    private static final Pattern GABARIT = Pattern.compile("\\{(?:(fichier|ctx|origine):)?([^{}]+)}");
    private static final Pattern ABSENT = Pattern.compile(
            "(?i)(empty|vide|aucun|not found|introuvable|no element|index 0 out of bounds|n'existe pas|does not exist)");
    private static final DataTable.TableConverter CONVERTISSEUR =
            new io.cucumber.datatable.DataTableTypeRegistryTableConverter(
                    new io.cucumber.datatable.DataTableTypeRegistry(java.util.Locale.ENGLISH));
    private static JsonNode catalogue;

    private final TestContext testContext;
    private final List<Entree> journal = new ArrayList<>();
    private Entree enCours;

    public NettoyageHooks(TestContext testContext) {
        this.testContext = testContext;
    }

    // ------------------------------------------------------------------------------------------------------------
    // Journal
    // ------------------------------------------------------------------------------------------------------------

    @Before(order = 0)
    public void debut() {
        journal.clear();
        enCours = null;
    }

    @BeforeStep
    public void avantStep() {
        JournalEtapes.EtapeCourante etape = JournalEtapes.COURANTE.get();
        enCours = null;
        if (etape == null) {
            return;
        }
        JsonNode definition = trouver(etape.texte);
        if (definition == null || "lecture".equals(texte(definition, "mode")) || "couvert".equals(texte(definition, "mode"))) {
            return;
        }
        enCours = new Entree(definition, lignes(etape.cellules));
        if ("document".equals(texte(definition, "lecteur"))) {
            try {
                lireDocument(enCours);
            } catch (Exception e) {
                enCours.erreurLecture = cause(e);
            }
        }
    }

    @AfterStep
    public void apresStep() {
        if (enCours == null) {
            return;
        }
        if ("fichiers".equals(texte(enCours.definition, "mode"))) {
            lireFichiers(enCours);
        }
        journal.add(enCours);
        enCours = null;
    }

    // ------------------------------------------------------------------------------------------------------------
    // Nettoyage (ordre inverse de création)
    // ------------------------------------------------------------------------------------------------------------

    @After(order = 0)
    public void nettoyer(Scenario scenario) {
        int ok = 0;
        int absents = 0;
        List<String> echecs = new ArrayList<>();
        List<Entree> inverse = new ArrayList<>(journal);
        Collections.reverse(inverse);
        for (Entree entree : inverse) {
            for (Action action : actions(entree)) {
                String resultat = executer(action);
                if (resultat == null) {
                    ok++;
                } else if (resultat.startsWith("absent")) {
                    absents++;
                } else {
                    echecs.add(action.objet + " (" + action.libelle + ") : " + resultat);
                }
            }
        }
        if (journal.isEmpty()) {
            return;
        }
        String bilan = "[NETTOYAGE] " + scenario.getName() + " : " + ok + " action(s) faite(s), " + absents
                + " objet(s) déjà absent(s), " + echecs.size() + " échec(s)"
                + (echecs.isEmpty() ? "" : " — " + String.join(" ; ", echecs));
        System.out.println(bilan);
        try {
            scenario.log(bilan);
        } catch (RuntimeException ignored) {
            // Journal du rapport indisponible : la console suffit
        }
        journal.clear();
    }

    private List<Action> actions(Entree entree) {
        List<Action> out = new ArrayList<>();
        String objet = texte(entree.definition, "objet");
        if ("document".equals(texte(entree.definition, "lecteur"))) {
            out.add(Action.restauration(objet, entree));
            return out;
        }
        if ("fichiers".equals(texte(entree.definition, "mode"))) {
            for (Map<String, String> fichier : entree.fichiers) {
                JsonNode regle = entree.regles.get(fichier.get("__regle"));
                ajouter(out, texte(regle, "objet"), regle, fichier);
            }
            return out;
        }
        for (Map<String, String> ligne : entree.lignes) {
            ajouter(out, objet, entree.definition, ligne);
        }
        return out;
    }

    /** Steps de nettoyage d'une définition ; un step répété avec le précédent (« repeter ») n'est pas ajouté seul. */
    private static void ajouter(List<Action> out, String objet, JsonNode definition, Map<String, String> valeurs) {
        List<String> repetes = new ArrayList<>();
        for (JsonNode etape : definition.path("nettoyage")) {
            if (repetes.contains(texte(etape, "step"))) {
                continue;
            }
            Action action = Action.step(objet, etape, valeurs);
            action.parent = definition;
            if (action.repeter != null) {
                repetes.add(action.repeter);
            }
            out.add(action);
        }
    }

    /** null si fait ; « absent : … » si l'objet n'existe plus ; sinon la cause de l'échec. */
    private String executer(Action action) {
        try {
            if (action.restauration != null) {
                restaurerDocument(action.restauration);
                return null;
            }
            if (action.repeter != null) {
                return repeter(action);
            }
            invoquer(action.etape, action.valeurs);
            return null;
        } catch (Exception e) {
            String cause = cause(e);
            return ABSENT.matcher(cause).find() ? "absent : " + cause : cause;
        }
    }

    /** Initialisation puis action suivante, tant que l'initialisation trouve un objet (stock en plusieurs contenus). */
    private String repeter(Action action) throws Exception {
        JsonNode suivante = null;
        for (JsonNode etape : action.parent.path("nettoyage")) {
            if (action.repeter.equals(texte(etape, "step"))) {
                suivante = etape;
            }
        }
        int faits = 0;
        for (int i = 0; i < MAX_REPETITIONS; i++) {
            try {
                invoquer(action.etape, action.valeurs);
            } catch (Exception e) {
                if (faits == 0) {
                    String cause = cause(e);
                    return ABSENT.matcher(cause).find() ? "absent : " + cause : cause;
                }
                return null;
            }
            if (suivante == null) {
                return null;
            }
            invoquer(suivante, action.valeurs);
            faits++;
        }
        return null;
    }

    /** Rejoue un step du Standard (classe et méthode du catalogue) avec une table construite par les gabarits. */
    private void invoquer(JsonNode etape, Map<String, String> valeurs) throws Exception {
        List<String> entetes = new ArrayList<>();
        List<String> ligne = new ArrayList<>();
        java.util.Iterator<Map.Entry<String, JsonNode>> colonnes = etape.path("table").fields();
        while (colonnes.hasNext()) {
            Map.Entry<String, JsonNode> colonne = colonnes.next();
            entetes.add(colonne.getKey());
            ligne.add(remplir(colonne.getValue().asText(), valeurs));
        }
        Class<?> classe = Class.forName(texte(etape, "classe"));
        Object instance = classe.getConstructor(TestContext.class).newInstance(testContext);
        Method methode = null;
        for (Method m : classe.getMethods()) {
            if (m.getName().equals(texte(etape, "methode")) && m.getParameterCount() == 1
                    && m.getParameterTypes()[0] == DataTable.class) {
                methode = m;
            }
        }
        if (methode == null) {
            throw new IllegalStateException("méthode " + texte(etape, "methode") + " absente de " + classe.getName());
        }
        List<List<String>> cellules = new ArrayList<>();
        cellules.add(entetes);
        cellules.add(ligne);
        try {
            // Convertisseur obligatoire : sans lui, asMaps() du step rejoué échoue (« created without a converter »)
            methode.invoke(instance, DataTable.create(cellules, CONVERTISSEUR));
        } catch (InvocationTargetException e) {
            throw e.getCause() instanceof Exception ? (Exception) e.getCause() : e;
        }
    }

    private String remplir(String gabarit, Map<String, String> valeurs) throws Exception {
        Matcher matcher = GABARIT.matcher(gabarit);
        StringBuffer out = new StringBuffer();
        while (matcher.find()) {
            String source = matcher.group(1);
            String nom = matcher.group(2);
            String valeur;
            if ("ctx".equals(source)) {
                valeur = valeurContexte(nom);
            } else if ("fichier".equals(source)) {
                valeur = valeurs.getOrDefault("fichier:" + nom, "");
            } else {
                valeur = valeurs.getOrDefault(nom, "");
            }
            matcher.appendReplacement(out, Matcher.quoteReplacement(valeur));
        }
        matcher.appendTail(out);
        return out.toString();
    }

    private String valeurContexte(String chemin) throws Exception {
        String[] parties = chemin.split("\\.");
        Object objet = testContext.getScenarioContext().getContext(Context.valueOf(parties[0]));
        for (int i = 1; i < parties.length && objet != null; i++) {
            objet = objet.getClass().getMethod("get" + Character.toUpperCase(parties[i].charAt(0))
                    + parties[i].substring(1)).invoke(objet);
        }
        if (objet instanceof Number && ((Number) objet).doubleValue() == Math.rint(((Number) objet).doubleValue())) {
            return String.valueOf(((Number) objet).longValue());
        }
        return objet == null ? "" : objet.toString();
    }

    // ------------------------------------------------------------------------------------------------------------
    // Fichiers intégrés (GS1) : valeurs créées lues dans le fichier
    // ------------------------------------------------------------------------------------------------------------

    private void lireFichiers(Entree entree) {
        ScenarioContext contexte = testContext.getScenarioContext();
        for (Map<String, String> ligne : entree.lignes) {
            try {
                String chemin = ManageFileUtil.transformFileToIntegrate(contexte, ligne);
                Document document = DocumentBuilderFactory.newInstance().newDocumentBuilder().parse(new File(chemin));
                XPath xpath = XPathFactory.newInstance().newXPath();
                int index = 0;
                for (JsonNode regle : entree.definition.path("fichiers")) {
                    String message = texte(regle, "message");
                    String cle = "regle" + index++;
                    entree.regles.put(cle, regle);
                    NodeList trouve = (NodeList) xpath.evaluate("//*[local-name()='" + message + "']", document,
                            XPathConstants.NODESET);
                    if (trouve.getLength() == 0) {
                        continue;
                    }
                    Map<String, String> valeurs = new LinkedHashMap<>(ligne);
                    valeurs.put("__regle", cle);
                    java.util.Iterator<Map.Entry<String, JsonNode>> champs = regle.path("valeurs").fields();
                    while (champs.hasNext()) {
                        Map.Entry<String, JsonNode> champ = champs.next();
                        valeurs.put("fichier:" + champ.getKey(),
                                xpath.evaluate(champ.getValue().asText(), document).trim());
                    }
                    entree.fichiers.add(valeurs);
                }
            } catch (Exception e) {
                System.out.println("[NETTOYAGE] Fichier " + ligne.get("fileName") + " illisible pour le journal : "
                        + cause(e));
            }
        }
    }

    // ------------------------------------------------------------------------------------------------------------
    // Document : valeur d'origine lue avant la modification, remise en fin de scénario
    // ------------------------------------------------------------------------------------------------------------

    private void lireDocument(Entree entree) throws Exception {
        RefReportDoc document = (RefReportDoc) testContext.getScenarioContext().getContext(Context.REF_REPORT_DOC);
        RefReportDocDraftResponse brouillon = RefReportDocEndPoints.getRefReportDocDraftResponse(document.getId());
        entree.documentId = document.getId();
        for (Map<String, String> ligne : entree.lignes) {
            for (String colonne : ligne.keySet()) {
                Method lecture = brouillon.getClass().getMethod("get" + Character.toUpperCase(colonne.charAt(0))
                        + colonne.substring(1));
                entree.origine.put(colonne, lecture.invoke(brouillon));
            }
        }
    }

    private void restaurerDocument(Entree entree) throws Exception {
        if (entree.documentId == null) {
            throw new IllegalStateException("valeur d'origine non lue avant la modification : " + entree.erreurLecture);
        }
        RefReportDocDraftResponse brouillon = RefReportDocEndPoints.getRefReportDocDraftResponse(entree.documentId);
        Properties proprietes = new Properties();
        proprietes.setReportsConfig(brouillon.getReportsConfig());
        proprietes.setVersion(brouillon.getVersion());
        proprietes.setId(brouillon.getId());
        for (Map.Entry<String, Object> origine : entree.origine.entrySet()) {
            String nom = "set" + Character.toUpperCase(origine.getKey().charAt(0)) + origine.getKey().substring(1);
            for (Method ecriture : proprietes.getClass().getMethods()) {
                if (ecriture.getName().equals(nom) && ecriture.getParameterCount() == 1) {
                    Object valeur = origine.getValue();
                    if (valeur == null && List.class.isAssignableFrom(ecriture.getParameterTypes()[0])) {
                        valeur = new ArrayList<>();
                    }
                    ecriture.invoke(proprietes, valeur);
                }
            }
        }
        RefReportDocDraftRequest requete = new RefReportDocDraftRequest();
        requete.setProperties(proprietes);
        requete.setState(new State(0, "UPDATE"));
        RefReportDocEndPoints.updateRefReportDoc(requete);
    }

    // ------------------------------------------------------------------------------------------------------------
    // Catalogue
    // ------------------------------------------------------------------------------------------------------------

    /** Catalogue de l'agent puis catalogue du projet (steps propres au client), fusionnés. */
    private static synchronized JsonNode catalogue() {
        if (catalogue == null) {
            ObjectMapper mapper = new ObjectMapper();
            com.fasterxml.jackson.databind.node.ArrayNode etapes = mapper.createArrayNode();
            for (String ressource : new String[]{CATALOGUE, CATALOGUE_PROJET}) {
                try (InputStream flux = NettoyageHooks.class.getClassLoader().getResourceAsStream(ressource)) {
                    if (flux != null) {
                        etapes.addAll((com.fasterxml.jackson.databind.node.ArrayNode) mapper.readTree(flux)
                                .path("etapes"));
                    }
                } catch (Exception e) {
                    System.out.println("[NETTOYAGE] Catalogue " + ressource + " illisible : " + cause(e));
                }
            }
            catalogue = mapper.createObjectNode().set("etapes", etapes);
        }
        return catalogue;
    }

    /** Entrée du catalogue d'un texte de step ({string}, {int}… : paramètres de l'expression Cucumber). */
    static JsonNode trouver(String texteStep) {
        for (JsonNode etape : catalogue().path("etapes")) {
            String expression = texte(etape, "step");
            String motif = Pattern.quote(expression)
                    .replace("{string}", "\\E\"[^\"]*\"\\Q").replace("{int}", "\\E-?\\d+\\Q")
                    .replace("{word}", "\\E\\S+\\Q");
            if (texteStep.trim().matches(motif)) {
                return etape;
            }
        }
        return null;
    }

    private static String texte(JsonNode noeud, String champ) {
        JsonNode valeur = noeud.get(champ);
        return valeur == null || valeur.isNull() ? null : valeur.asText();
    }

    private static List<Map<String, String>> lignes(List<List<String>> cellules) {
        List<Map<String, String>> out = new ArrayList<>();
        if (cellules.size() < 2) {
            return out;
        }
        List<String> entetes = cellules.get(0);
        for (List<String> ligne : cellules.subList(1, cellules.size())) {
            Map<String, String> valeurs = new LinkedHashMap<>();
            for (int i = 0; i < entetes.size() && i < ligne.size(); i++) {
                valeurs.put(entetes.get(i), ligne.get(i));
            }
            out.add(valeurs);
        }
        return out;
    }

    private static String cause(Throwable e) {
        Throwable racine = e;
        while (racine.getCause() != null && racine.getCause() != racine) {
            racine = racine.getCause();
        }
        String message = racine.getMessage() == null ? racine.getClass().getSimpleName() : racine.getMessage();
        return message.replaceAll("\\s+", " ").trim();
    }

    // ------------------------------------------------------------------------------------------------------------

    private static final class Entree {
        final JsonNode definition;
        final List<Map<String, String>> lignes;
        final List<Map<String, String>> fichiers = new ArrayList<>();
        final Map<String, JsonNode> regles = new LinkedHashMap<>();
        final Map<String, Object> origine = new LinkedHashMap<>();
        String documentId;
        String erreurLecture;

        Entree(JsonNode definition, List<Map<String, String>> lignes) {
            this.definition = definition;
            this.lignes = lignes;
        }
    }

    private static final class Action {
        String objet;
        String libelle;
        JsonNode etape;
        JsonNode parent;
        String repeter;
        Map<String, String> valeurs;
        Entree restauration;

        static Action step(String objet, JsonNode etape, Map<String, String> valeurs) {
            Action action = new Action();
            action.objet = objet;
            action.libelle = texte(etape, "step");
            action.etape = etape;
            action.valeurs = valeurs;
            action.repeter = texte(etape, "repeter");
            return action;
        }

        static Action restauration(String objet, Entree entree) {
            Action action = new Action();
            action.objet = objet;
            action.libelle = "remise de la valeur d'origine";
            action.restauration = entree;
            return action;
        }
    }
}
