import argparse
import io
import json
import logging
import os
import subprocess
import sys
import time
import requests
from dotenv import load_dotenv

from agent_socle import env_tools
from tools import assistant
# Fonctions communes des CLI (Claude Code, prompts, tokens, doc ODATiO) et base REST Bitbucket : socle
from agent_socle import cli
from agent_socle.cli import (_env_int,
    claude_effort,
    render_prompt,
    lean_session,
    run_claude,
    report_token_usage,
    share_knowledge)

# Force l'encodage UTF-8 sous Windows
if hasattr(sys.stdout, "buffer"):
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
if hasattr(sys.stderr, "buffer"):
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8")

# Lignes du .env en défaut : une phrase avec leurs numéros au lieu des avertissements bruts de python-dotenv
logging.getLogger("dotenv.main").setLevel(logging.ERROR)
load_dotenv()
if env_tools.check_env(".env"):
    print(f"[CONFIGURATION] ⚠️ {env_tools.check_line(env_tools.check_env('.env'))}")


# ---------------------------------------------------------------------------
# Économie de tokens
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# Modèles de prompts (prompts/*.md, personnalisables via PROMPTS_DIR)
# ---------------------------------------------------------------------------

DEFAULT_PROMPTS_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "prompts")
cli.DEFAULT_PROMPTS_DIR = DEFAULT_PROMPTS_DIR  # puis les modèles communs du socle


# ---------------------------------------------------------------------------
# Release note & base de données
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# Connaissances partagées : dépôt des analyses produites (knowledge_tools), jamais bloquant
# ---------------------------------------------------------------------------


def run_assistant(question: str, answer_file: str) -> int:
    """
    Onglet « Assistant » : réponse à une question libre sur les fonctionnalités de l'application ou ses résultats,
    par l'agent en lecture seule (code de l'application, résultats de .tmp/ et livrables). La réponse est écrite
    dans `answer_file` (Markdown, secrets masqués). Renvoie le code de sortie.
    """
    question = (question or "").strip()
    if not question:
        print("[ASSISTANT] ❌ Question vide.")
        return 2
    code_dir = os.path.dirname(os.path.abspath(__file__))
    prompt = render_prompt(
        "assistant", utilisateur=os.getenv("JIRA_EMAIL", "").split("@")[0] or "l'utilisateur", code=code_dir,
        token_saving_rules="\n" + render_prompt("token_saving_rules"), resultats=assistant.results_index(),
        historique=assistant.recent_exchanges(assistant.load_history()[:-1]), question=question)
    model = os.getenv("CLAUDE_MODEL_ASSISTANT", "").strip() or "sonnet"
    effort = claude_effort("CLAUDE_EFFORT_ASSISTANT")
    returncode, output, usage, limit_hit = run_claude(prompt, model, [code_dir], REVIEW_TOOLS,
                                                      extra_args=("--max-turns", str(_env_int("ASSISTANT_MAX_TURNS", 25))),
                                                      cwd=code_dir, effort=effort, lean=lean_session())
    report_token_usage("ASSISTANT", "ask", model, usage, len(prompt), returncode, effort)
    answer = assistant.clean_answer(output)
    if limit_hit or not answer:
        print("[ASSISTANT] ❌ Pas de réponse" + (" (quota atteint)." if limit_hit else "."))
        return 1
    if answer_file:
        os.makedirs(os.path.dirname(os.path.abspath(answer_file)), exist_ok=True)
        with open(answer_file, "w", encoding="utf-8") as f:
            f.write(answer)
    # Analyse déposée dans le répertoire de connaissances partagé (projet du client cité, sinon projet par défaut)
    project, client = assistant.subject_project(question, answer)
    share_knowledge(assistant.analysis_markdown(question, answer), project, "analyse-assistant",
                    " ".join(question.split())[:90], client=client, tags=["assistant"] + ([client] if client else []))
    return 0


REVIEW_TOOLS = ["Read", "Grep", "Glob"]


def main():
    parser = argparse.ArgumentParser(description="Agent Scrum Master - Cadrage, Patching & Cucumber")
    parser.add_argument("--issue", default="", help="Clé du ticket Jira (ex: PROJ-123) ; inutile pour generate-cucumber-client")
    parser.add_argument("--project", default="", help="Clé du projet Jira (ex: PROJ) ; inutile pour generate-cucumber-client")
    parser.add_argument("--client-repo", default="", help="Chemin local ou URL du dépôt client")
    parser.add_argument("--std-repo", default="", help="Chemin local ou URL du dépôt socle standard")
    parser.add_argument("--step", choices=["cadrage", "epic-cadrage", "epic-sync", "refine", "patch", "generate-cucumber-postmerge", "besoin", "cadrage-reponses", "generate-cucumber-client", "review-pr", "ask", "restore-config"], default="cadrage", help="Étape du traitement (epic-cadrage : découpe une épopée en tâches enfants analysées ; besoin : reformulation du besoin et questions ouvertes ; cadrage-reponses : complète le cadrage avec les réponses de l'onglet Intégration ; generate-cucumber-client : patch de tests Cucumber couvrant tous les spécifiques d'un client)")
    parser.add_argument("--pr-url", action="append", default=[],
                        help="review-pr : adresse d'une pull request de la fonctionnalité (option répétable : toutes les "
                             "PR de la fonctionnalité sont relues ensemble)")
    parser.add_argument("--feature", default="", help="review-pr : ticket Jira de la fonctionnalité relue")
    parser.add_argument("--source-schema", default="", help="restore-config : schéma de la config client (base client)")
    parser.add_argument("--target-schema", default="", help="restore-config : schéma des tests Cucumber à remplacer")
    parser.add_argument("--backup", default="", help="restore-config : sauvegarde à remettre en place (retour arrière)")
    parser.add_argument("--copied-reference", action="store_true", help="restore-config : configuration copiée — copie dans le schéma de test des runs du profil puis instantané de référence")
    parser.add_argument("--question", default="", help="ask : question posée à l'assistant (onglet Assistant)")
    parser.add_argument("--answer-file", default="", help="ask : fichier où écrire la réponse")
    parser.add_argument("--no-init", action="store_true", help="generate-cucumber-client : aucune init lancée avant de juger les lots (données du client déjà présentes dans l'environnement des runs Jenkins)")
    parser.add_argument("--specifics", default="", help="generate-cucumber-client : ids des spécifiques à traiter, séparés par des virgules (défaut : non couverts et partiels de l'inventaire)")
    parser.add_argument("--resume", action="store_true", help="generate-cucumber-client : reprend les lots non terminés de la dernière génération")
    parser.add_argument("--reprise", action="store_true", help="generate-cucumber-client : relance --specifics déjà générés en reprenant leur test existant (vérification puis correction, jamais régénéré) ; un test déjà vert n'est pas relancé ; un spécifique sans test est généré en complément")
    parser.add_argument("--complete", action="store_true", help="generate-cucumber-client : complète la dernière génération (worktree et lots conservés) par de nouveaux lots pour --specifics, à défaut pour les spécifiques non couverts faute de step, d'endpoint, d'interaction ou de vérification")
    parser.add_argument("--max-lots", type=int, default=0, help="generate-cucumber-client : nombre maximal de lots traités par cette exécution (0 = tous), les suivants restent à reprendre")
    parser.add_argument("--inventory-only", action="store_true", help="generate-cucumber-client : pré-calculs seuls (inventaire, catalogues, couverture), sans appel à Claude")
    parser.add_argument("--init-only", action="store_true", help="generate-cucumber-client : lot d'initialisation des données, en patch séparé, selon l'état détecté de l'init : absente, copie des Init_<MODULE> du Cucumber Standard pour ses modules ; incomplète, compléments seuls dans une nouvelle feature (jamais de réécriture ni de fichier protégé modifié) ; complète, rien. Sans appel à Claude, sauf steps du projet à écrire")
    parser.add_argument("--profile-only", action="store_true", help="generate-cucumber-client : détecte le profil client (racine et tag des features, init, clear, fichiers du module spécifique, runner, codes de référence, version de la bibliothèque) dans le dépôt <client>-cucumber, sans appel à Claude")
    parser.add_argument("--detect-only", action="store_true", help="generate-cucumber-client : détection automatique du projet (inventaire, profil client, faits de l'initialisation des données), mise en cache par empreinte des commits des dépôts et sautée si elle n'a pas changé ; lecture seule, sans appel à Claude")
    parser.add_argument("--align-library", action="store_true", help="generate-cucumber-client : bibliothèque Cucumber du pom.xml alignée sur la version du Standard du client (Jenkins, à défaut l'application) : seule la version change dans le pom, compilation corrigée dans les steps du projet, dry-run, puis (livraison autonome) runs de l'init et complet, promotion sans régression")
    parser.add_argument("--simple-depot", default="", choices=["", "verifier", "creer", "branche", "aligner"], help="generate-cucumber-client, onglet « Cucumber » simplifié, étape 2 : dépôt <client>-cucumber — constat, création (droits contrôlés), branche de la version, alignement validé de la bibliothèque et du runner, compilation")
    parser.add_argument("--anticipation", action="store_true", help="generate-cucumber-client : analyse statique d'anticipation (Studio à la version testée : variables d'entrée non déclarées, composants absents, XML), 0 token, sans run ; résultat dans anticipation.json")
    parser.add_argument("--simple-init", default="", choices=["", "auto", "verifier", "appliquer", "completer", "lancer", "corriger"], help="generate-cucumber-client, onglet « Cucumber » simplifié, étape 4 : init du client sur la branche de la version — clear déduit des routes de l'application à sa version, init du Standard adaptée ou init existante comparée, run Jenkins de l'init, correction connue proposée sur échec ; « auto » enchaîne tout (fichiers générés et poussés, init lancée sur Jenkins) jusqu'à une init verte")
    parser.add_argument("--java21", action="store_true", help="generate-cucumber-client : migration Java 21 du projet de tests (pom.xml seul, branche dédiée jamais fusionnée) et contrôle de non-régression : compilation et dry-run en Java 21, runs AVANT (application Java 11) et APRÈS (application Java 21), relance des écarts, rapport, PR proposée sans régression")
    parser.add_argument("--java21-avant", default="", help="--java21 : environnement AVANT confirmé, « composant_version=<v>;kis_version=<v> »")
    parser.add_argument("--java21-apres", default="", help="--java21 : environnement APRÈS confirmé, « composant_version=<v>;kis_version=<v> »")
    parser.add_argument("--module-tag", default="", help="generate-cucumber-client : module exclu du périmètre testé (ex. TMS) : son tag client est remplacé par @MODULE_EXCLU dans ses features (runs Jenkins lancés à la main), commit dédié et patch <client>-cucumber-module-<mod>-exclu.patch, sans appel à Claude")
    parser.add_argument("--module-tag-restore", action="store_true", help="avec --module-tag : opération inverse à la réactivation du module (@MODULE_EXCLU -> tag client), patch <client>-cucumber-module-<mod>-reactive.patch")
    parser.add_argument("--fix-client-tags", action="store_true", help="generate-cucumber-client : règle rétroactive « tag client » : ajoute le tag client au-dessus des scénarios actifs qui ne l'ont pas (scénarios désactivés exclus), commite dans le worktree, contrôle de nouveau et régénère le patch, sans appel à Claude")
    parser.add_argument("--detect-force", action="store_true", help="generate-cucumber-client --detect-only : détection refaite même si l'empreinte des commits n'a pas changé (profil corrigé)")
    parser.add_argument("--health-only", action="store_true", help="generate-cucumber-client : bilan de santé du dépôt <client>-cucumber avant génération (emplacement et tags des features, fichiers de données manquants, compilation et dry-run de toutes ses features), sans appel à Claude")
    parser.add_argument("--import-run", nargs="*", default=None, help="generate-cucumber-client : import d'un rapport d'exécution (console Jenkins, rapport Cucumber JSON, exports de logs serveur CSV), secrets masqués avant stockage, analysé sans appel à Claude")
    parser.add_argument("--import-url", default="", help="generate-cucumber-client : import d'un build Jenkins (console et rapports Cucumber JSON lus en mémoire), analysé sans appel à Claude")
    parser.add_argument("--patch-only", action="store_true", help="generate-cucumber-client : régénère seulement le patch <client>-cucumber.patch depuis l'espace de travail de l'agent (un commit, écart net avec la branche d'intégration actuelle), sans appel à Claude")
    parser.add_argument("--fix-init-from-import", type=int, default=0, help="generate-cucumber-client : l'init du run importé n° N échoue dans les compléments écrits par l'agent : il corrige ces fichiers seuls (contrôlés), puis rejoue l'init si CUCUMBER_AGENT_JENKINS=1, sinon livre <client>-cucumber-init-correction.patch")
    parser.add_argument("--modules-exclus", default=None, help="generate-cucumber-client : modules exclus du périmètre testé (codes séparés par des virgules, ex. TMS ; vide : aucun), transmis par l'IHM depuis le profil client ; enregistrés dans le profil s'ils en diffèrent, affichés en tête du journal")
    parser.add_argument("--from-import", type=int, default=0, help="generate-cucumber-client --complete : lots « correction » des échecs « test » des scénarios de l'agent de l'import n° N")
    parser.add_argument("--run-jenkins", action="store_true", help="generate-cucumber-client : pousse le worktree sur la branche lue par le job (profil), contrôle le commit, lance le job Jenkins du client, suit le build puis importe sa console et ses rapports (bilan)")
    parser.add_argument("--run-dry", action="store_true", help="generate-cucumber-client --run-jenkins : aperçu sans push ni lancement (0 token) — job, branche, rôles des paramètres, paramètres envoyés (secrets masqués) et nombre de tests attendus pour --run-tags")
    parser.add_argument("--run-no-push", action="store_true", help="generate-cucumber-client --run-jenkins : rien n'est commité ni poussé, le job exécute la branche telle qu'elle est sur le dépôt client (--run-branch, à défaut celle du profil) ; lecture seule")
    parser.add_argument("--run-branch", default="", help="generate-cucumber-client --run-jenkins --run-no-push : branche du dépôt client à exécuter (défaut : branche de travail du profil)")
    parser.add_argument("--run-tags", default="", help="generate-cucumber-client --run-jenkins : expression de tags ajoutée aux arguments du job (-Dcucumber.filter.tags, vide : tous les scénarios actifs)")
    parser.add_argument("--server-log", default="", help="generate-cucumber-client : export de logs serveur à contrôler (avec --log-scenario) : couvre-t-il l'heure du step en échec ? (l'outil d'export peut garder les seules lignes les plus récentes), sans appel à Claude")
    parser.add_argument("--log-scenario", default="", help="generate-cucumber-client --server-log : identifiant ou tag @agent_… du scénario en échec (dernière génération)")
    parser.add_argument("--init-modules", default="", help="generate-cucumber-client --init-only : modules retenus parmi WMS, TMS, OMS, séparés par des virgules (défaut : détectés dans les dépôts et l'inventaire du client)")
    parser.add_argument("--client-name", default="", help="generate-cucumber-client : nom du client, qui donne le dépôt cible <client>-cucumber (défaut : déduit de --client-repo)")
    parser.add_argument("--cucumber-std-repo", default="", help="Dépôt du Cucumber Standard : URL Bitbucket (navigation ou clonable) ou chemin d'un clone local (défaut : BITBUCKET_CUCUMBER_STD_REPO)")
    parser.add_argument("--feedback", default="", help="Remarques de l'utilisateur pour la révision")
    # Les tests Cucumber sont désormais traités après merge, par le ticket SPEC [CUCUMBER] (generate-cucumber-postmerge)
    parser.add_argument("--generate-cucumber", action="store_true", default=False, help="Réactiver ponctuellement la génération des tests Cucumber avec le patch")
    parser.add_argument("--skip-cucumber", dest="generate_cucumber", action="store_false", help="Désactiver la génération des tests Cucumber (comportement par défaut)")
    parser.add_argument("--db-schema", default="", help="Schéma de la base client consultable en lecture seule par l'agent (vide = pas d'accès base)")
    parser.add_argument("--application", default="", help="Étapes besoin et generate-cucumber-client : Projet / Application dont les tickets servent de référence, séparés par des virgules (étape besoin, défaut : valeur du champ JIRA_APPLICATION_FIELD du ticket)")
    parser.add_argument("--epic-task-type", default=os.getenv("JIRA_EPIC_TASK_ISSUE_TYPE", ""), help="Type des tâches créées sous une épopée (défaut : premier type existant du projet parmi Tâche, Task, Development, Story)")

    postmerge = parser.add_argument_group("Post-merge (--step generate-cucumber-postmerge, déclenché par webhook_server.py)")
    postmerge.add_argument("--repo-slug", default="", help="Dépôt Bitbucket fusionné (ex: odatio-client)")
    postmerge.add_argument("--merge-commit", default="", help="Commit de merge (ou toHash du push)")
    postmerge.add_argument("--base-commit", default="", help="Commit de référence du diff (défaut : 1er parent du merge)")
    postmerge.add_argument("--source-branch", default="")
    postmerge.add_argument("--target-branch", default="")
    postmerge.add_argument("--cucumber-repo", default="", help="Dépôt Cucumber client (défaut : POSTMERGE_CUCUMBER_REPOS puis détection automatique)")
    postmerge.add_argument("--cucumber-base-branch", default=os.getenv("POSTMERGE_CUCUMBER_BASE_BRANCH", "integration"))
    postmerge.add_argument("--jira-target-project",
                           default=os.getenv("POSTMERGE_JIRA_PROJECT", "").strip() or os.getenv("JIRA_PROJECT_KEY", "").strip())
    # Composant ou équipe Atlassian des tickets créés (post-merge et tâches d'épopée)
    postmerge.add_argument("--jira-component", default=os.getenv("POSTMERGE_JIRA_COMPONENT", "").strip())
    postmerge.add_argument("--jira-label", default=os.getenv("POSTMERGE_JIRA_LABEL", "").strip())

    args = parser.parse_args()
    # Étapes des agents spécialisés : leur CLI, relancée avec les mêmes arguments (AGENT_<NOM>_DIR)
    if args.step == "review-pr":
        sys.exit(cli.deleguer("revue-pr", sys.argv[1:]))
    if args.step in ("cadrage", "refine", "epic-cadrage", "epic-sync", "patch"):
        sys.exit(cli.deleguer("lancement", sys.argv[1:]))
    if args.step in ("besoin", "cadrage-reponses"):
        sys.exit(cli.deleguer("integration", sys.argv[1:]))
    if args.step in ("generate-cucumber-client", "generate-cucumber-postmerge", "restore-config"):
        sys.exit(cli.deleguer("cucumber", sys.argv[1:]))
    sys.exit(run_assistant(args.question, args.answer_file))


# Points restants et dépendances non vérifiables par l'API déclarés par Claude à la dernière écriture de steps
# d'init, par libellé (résultat de la préparation)
INIT_REMAINING, INIT_UNVERIFIABLE = {}, {}


if __name__ == "__main__":
    # Étapes Cucumber (garde de livraison DeliveryBlocked comprise) : dans le processus d'agent-cucumber
    main()