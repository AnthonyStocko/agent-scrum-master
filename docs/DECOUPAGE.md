# Découpage d'agent-scrum-master en agents spécialisés

Phase 0 : cartographie produite par script, sans toucher au code.
Sources : `scripts/inventaire.py` → `docs/inventaire.csv` (fonctions, plages de lignes, imports, appels, clés
`st.session_state`, variables d'environnement) ; `scripts/atteignables.py app.py|main.py` →
`docs/atteignables-app.csv` et `docs/atteignables-main.csv` (fonctions de premier niveau atteintes depuis chaque
page `if PAGE == …` et chaque étape `--step`, y compris par les drapeaux `is_cucumber_client`, `is_postmerge`,
`is_besoin`, `is_cadrage_answers`). Ces trois CSV sont la référence des déplacements fonction par fonction.

## 1. Dépôts

| Agent | Dépôt (privé, `C:\…`) | Paquet Python | IHM / API | CLI |
|---|---|---|---|---|
| Socle | `agent-socle` | `agent_socle` (installé `pip install -e ..\agent-socle`, puis tag `v0.1.0`) | — | — |
| Projets et connexions | `agent-projets` | racine | 8504 / 8512 | `main.py --step api\|projets` |
| Intégration | `agent-integration` | `tools/` | 8505 | `main.py --step besoin\|cadrage-reponses` |
| Lancement | `agent-lancement` | `tools/` | 8506 | `cadrage\|refine\|epic-cadrage\|epic-sync\|patch` |
| Cucumber | `agent-cucumber` | `tools/` | 8507 | `generate-cucumber-client\|generate-cucumber-postmerge\|restore-config` |
| Revue de PR | `agent-revue-pr` | `tools/` | 8508 | `review-pr` |
| Manager | `agent-manager-savoye` (existant) | — | 8500 (superviseur 8499) | `ask` |

Aucun autre dépôt. Les modules gardent leur nom (seul le chemin d'import change : `tools.X` → `agent_socle.X`).

## 2. Répartition des modules `tools/` (105, dont 69 `cucumber_*`)

| Destination | Modules |
|---|---|
| Socle | `token_measure`, `token_report`, `ui_timer`, `jira_cli`, `jira_guard`, `jira_tools` (voir §4), `jenkins_tools`, `bitbucket_std`, `bitbucket_code`, `db_tools`, `secret_tools`, `env_tools`, `auth_tools`, `auth_setup`, `lien_manager`, `mcp_servers`, `groundcover_tools`, `odatio_docs`, `skills`, `knowledge_tools`, `memory_tools`, `livraisons` ; extraits `git_tools` (`cucumber_tools`) et `horodatage` (`cucumber_verify`) ; `chemins` (`AGENT_DATA_DIR`) |
| Projets | tests de connexion de la page Configuration ; registre en lecture servi par 8512 (code dans `agent_socle.projets`) ; `config_commune` → socle (v0.2.0) |
| Intégration | `integration_tools` |
| Lancement | `analysis_publication` (lecture du statut extraite au socle, §4), `estimation`, `epic_tools` |
| Cucumber | les 69 `cucumber_*` (dont `cucumber_lock` et `cucumber_cost`, cf. §7), `client_version`, `standard_version`, `standard_version_task`, `cucumber_detection`, `db_restore`, `xlsm_tool`, `tools/data/cucumber_nettoyage/` |
| Revue de PR | `pr_review` (base REST Bitbucket extraite au socle, §4) |
| Manager | `assistant`, `poller_control`, `lecture_manager` |

Écarts avec le tableau cible, tous imposés par « jamais d'import d'un autre agent » :

| Module | Cible annoncée | Classé | Raison (utilisateurs hors de l'agent cible) |
|---|---|---|---|
| `jira_guard`, `skills`, `memory_tools` | Lancement | Socle | `run_claude` (toutes étapes) ; `memory_tools` lu par `knowledge_tools` et `cucumber_verify` |
| `livraisons` | Lancement | Socle | `lecture_manager` (manager) |
| `env_tools`, `auth_tools`, `auth_setup` | Projets | Socle | contrôle `.env` au démarrage de chaque CLI ; `require_login`/`current_author` dans chaque IHM |
| `mcp_servers` | Projets | Socle | `run_claude`/`claude_command` (toutes étapes) ; sa config passera par le client 8512 en phase 2 |
| `jenkins_tools`, `db_tools`, `groundcover_tools` | Cucumber | Socle | lus par manager, projets (test de connexion) et `run_claude` (`mask`) |
| `knowledge_tools`, `odatio_docs`, `lien_manager` | Manager | Socle | appelés par toutes les étapes / lus à l'entrée de chaque IHM ; le manager garde les pages |
| `bitbucket_std`, `bitbucket_code` | Revue de PR | Socle | cadrage (lancement) et Cucumber |
| `client_version`, `cucumber_detection` | Projets | Cucumber | dépendent de `cucumber_job`, `cucumber_profile`, `cucumber_incidents`… ; leur IHM n'est que sur les pages Cucumber |

## 3. `app.py` et `main.py` : règle de partage

Chaque fonction va à l'agent de ses entrées (colonne `entrees` des CSV `atteignables-*`).

| Entrée(s) | `app.py` (pages) | `main.py` (étapes) |
|---|---|---|
| une seule entrée | page → agent du §1 | étape → agent du §1 |
| plusieurs agents, ou `commun` | socle (`agent_socle.ihm` / `agent_socle.cli`) | socle |
| `aucune` | reste avec le fichier d'origine (vérifiée à la main) | idem |

Cas d'`app.py` : `run_main_step`, `run_log_path`, `journal_phrases`, `interruption_cause`, `render_technical_journal`,
`search_criteria_form`, `search_jira_tickets`, `keep_page_state`, `page_changed`, `render_navigation`,
`agent_version`, `apply_manager_link`, `render_manager_link`, `env_int` → socle. Fonctions marquées `commun` mais
seulement référencées par le dictionnaire des étapes simples (`render_simple_*`, `render_client_version`,
`render_standard_version*`, `render_tested_modules`, `render_backup_restore`, `short_date`) → Cucumber.
Page `configuration` : `render_estimation_settings` → Lancement (section Réglages de sa page) ;
`claude_account`, `claude_config_dirs` → Manager (compte de l'utilisateur) ; le reste → Projets.

Cas de `main.py` : chaîne Claude (`run_claude`, `claude_command`, `claude_env`, `claude_effort`, `parse_claude_output`,
`lean_session`, `budget_hit`, `cucumber_budget`, `usage_limit_*`, `report_token_usage`, `render_prompt`, `_read_text`,
`_env_int`), connaissances (`share_knowledge`, `share_file`, `consult_knowledge`, `flush_knowledge_outbox`,
`produced`), dépôts locaux (`ensure_local_repo`, `ensure_cucumber_std_repo`, `checkout_cucumber_std_ref`,
`update_clone`, `sync_working_copy`, `fetch_*`, `_git_out`, `_force_rmtree`, `_same_git_url`, `cucumber_std_clone_dir`,
`detect_odatio_version`, `odatio_version_from_pom`, `bitbucket_base_url`, `cadrage_remote_scope`),
`format_jira_context`, `ticket_client`, `build_db_block`, `_build_db_block`, `odatio_docs_block`,
`odatio_docs_tools`, `print_draft_status`, `check_jira_untouched`, `_safe_memory` → socle.

`main()` (l. 1578-2182) mêle toutes les étapes : chaque agent reçoit une copie produite par script (ast) dont sont
retirées les branches `args.step == …` / `is_*` des autres étapes, et `--step` restreint à ses `choices`. Aucune ligne
gardée n'est modifiée.

## 4. Fonctions extraites d'un module vers le socle (re-exportées par le module d'origine)

| Module d'origine | Fonctions → socle | Utilisateur hors agent |
|---|---|---|
| `cucumber_tools` | fait (phase 1) : `AGENT_GIT_NAME`, `AGENT_GIT_EMAIL`, `_COMMITTING`, `agent_identity`, `_git`, `git_repos`, `pick_ref` → `agent_socle.git_tools` ; en phase 3 selon `main.py` commun : `client_name_from_repo`, `STD_REF_LOG`, `work_id`… | `bitbucket_std`, `main.py` commun |
| `pr_review` | `base_url`, `_headers`, `configured`, `BitbucketError`, `create_pull_request` | `cucumber_repo_setup`, Cucumber (Java 21) |
| `analysis_publication` | `cadrage_path`, `status` et leurs aides (`load_record`, `_fingerprint`, `_read`) | `lecture_manager` |
| `cucumber_verify` | fait (phase 1) : `_timestamp` → `agent_socle.horodatage` | `groundcover_tools` |
| `cucumber_delivery` | `disable_push` | `ensure_local_repo` (socle) |
| `client_version` | `cucumber_std_ref`, `cucumber_std_names` | `ensure_local_repo` (socle) |
| `standard_version` | `normalize` | `ensure_local_repo` (socle) |
| `jira_tools` | tout, sauf `update_jira_section` (→ Intégration) | tous les agents |
| `client_version` | `saved`, `chosen`, `client_git_ref`, `line`, `FIELD` (lecture) → Projets, servies par 8512 | Lancement (cadrage, `main()` l. 1761) |
| `cucumber_detection` | `known_projects`, `save_project` → Projets | Cucumber, configuration |

L'extraction de fonctions (script, plages de lignes de `inventaire.csv`) précède le déplacement du module ;
le module d'origine les importe depuis le socle : comportement et noms inchangés.

## 5. Prompts (34) et fichiers annexes

| Destination | Fichiers |
|---|---|
| Socle | `token_saving_rules.md`, `odatio_docs.md`, `db_access.md` |
| Intégration | `besoin.md`, `cadrage_reponses.md` |
| Lancement | `cadrage.md`, `refine.md`, `epic_cadrage.md`, `epic_revision.md`, `patch.md`, `release_note.md`, `bitbucket_code_cadrage.md`, `std_bitbucket_code.md` |
| Cucumber | `cucumber_*.md` (14), `patch_cucumber.md`, `postmerge.md`, `logs_access.md`, `logs_mcp_access.md`, `env_purge_logs.md`, `skill_extraction.md`, `jenkins/`, `tools/data/cucumber_nettoyage/` |
| Revue de PR | `pr_review.md`, `odatio-specific-code-conventions-dev.md` (lu par `run_pr_review`, `DEV_CONVENTIONS_PATH`) |
| Manager | `assistant.md`, `webhook_server.py`, `mcp_lecture.py` |

`cadrage.md`, `patch.md`, `besoin.md` sont aussi lus (chemin seulement) par des modules d'autres agents :
lecture par chemin via `AGENT_DATA_DIR`/dépôt d'origine à traiter dans la phase de l'agent qui part en premier.

## 6. État partagé

| État | Où | Règle |
|---|---|---|
| `.tmp/` (`cucumber`, `cucumber_std`, `memory`, `logs`, `webhook`, `pr_review`, `publication`, `restauration`, `sauvegardes`, `knowledge_outbox`, `token_usage.csv`, `livraisons.jsonl`…) | `C:\agent-scrum-master\.tmp` | ne bouge pas ; `agent_socle.chemins` le résout par `AGENT_DATA_DIR` (défaut : `..\agent-scrum-master`) |
| Fichiers produits à la racine (`<CLE>-cadrage.md`, `*.patch`, `*-release-note.md`, `*-decoupage.json`) | racine d'agent-scrum-master | idem `AGENT_DATA_DIR` |
| `.env` | racine d'agent-scrum-master | lu depuis `AGENT_DATA_DIR` ; jamais copié |
| Racine calculée par `abspath(__file__)` | 35 fichiers (10 `WORKSPACE_DIR`) | remplacée par script : données → `AGENT_DATA_DIR`, code (prompts, `xlsm_tool`, `tools/data`) → dépôt de l'agent |
| `st.session_state` partagé | `PERSISTENT_KEYS`, `NAV_KEY`, `workflow_step`, connexion OIDC | `PERSISTENT_KEYS` : `cucumber_*`/`cs_*` → Cucumber, `integ_*` → Intégration, recherche → Lancement + Intégration (chacun sa copie) |

## 7. Décisions de phase 0

| Sujet | Décision |
|---|---|
| Ports (phase 2, choix de l'utilisateur) | 8499-8503 déjà pris (superviseur, manager, scrum-master, odatio, dev-tool) : nouveaux agents sur 8504-8508, API 8512 ; IHM en 127.0.0.1 |
| Registre des projets (phase 2, choix de l'utilisateur) | façade en lecture sans migration : projets Cucumber (`.tmp/cucumber`) + applications de `commun.toml` du manager, sans fusion (`agent_socle.projets`, client 8512 avec repli local) ; le manager reste l'éditeur de `commun.toml` |
| Secrets (phase 2, choix de l'utilisateur) | aucun ne bouge : `.env` de `AGENT_DATA_DIR` et injection par le manager (`SAVOYE_VARIABLES`) ; jetons Bitbucket par usage reportés |
| Page Configuration (phase 2) | tests de connexion → agent-projets ; le formulaire global `.env` reste dans scrum-master (réglages de tous les agents, valeurs par défaut de `cucumber_tools`) et se répartit avec chaque agent (phases 3-7) ; réglages d'estimation, compte Claude et mémoire restent jusqu'aux phases 5 et 7 |
| OIDC des nouvelles IHM | `[secrets] files` pointe le `secrets.toml` d'agent-scrum-master (lu sur place, pas de copie) |
| API d'agent-projets (8512) | `http.server` de la bibliothèque standard (lecture JSON) : `fastapi` absent du venv, aucune dépendance ajoutée |
| `AGENT_DATA_DIR` | nouvelle variable ; défaut `..\agent-scrum-master` ; aucune migration de `.tmp/` |
| Délégation CLI | `agent-scrum-master/main.py --step X` relance `<dépôt de l'agent>\main.py` avec les mêmes arguments (`AGENT_<NOM>_DIR`, défaut `C:\agent-<nom>`) |
| Tests | suivent leur code (`tests/test_<module>.py`) ; patchs `tools.X` réécrits par script ; un test par dépôt vérifie l'absence d'import d'un autre agent |
| Découpage futur de Cucumber | proposé en phase 6 seulement (génération, vérification, livraison) |
| `cucumber_lock`, `cucumber_cost` (phase 1) | restent dans Cucumber : `cucumber_cost` n'est appelé que par Cucumber ; `cucumber_lock` dépend de `cucumber_tools.cache_dir`/`CACHE_ROOT` (remplacé par 10 tests) — verrou générique au socle en phase 8, lecture par le manager en phase 7 |
| Fonctions de `main.py`/`app.py` pour le socle (§3) | extraites en phase 3, avec le premier agent qui les utilise ; `jira_tools.update_jira_section` → Intégration en phase 4 |
| Chemins des CLI lancées par Claude | `main.py` : `tools/<module>.py` → `<module>.__file__` (fichier du socle) ; seul le chemin change |
| Tests liés aux documents de l'application (`.env.example`, `README.md`, `.env`) | dans le socle, lus sous `chemins.DATA_DIR` (mêmes fichiers) |
| Socle des CLI et des IHM (phase 3) | `agent_socle.cli` : fonctions de `main.py` communes aux étapes (`run_claude`, `render_prompt`, `report_token_usage`, doc ODATiO, `share_knowledge`…), importées par chaque `main.py` sous le même nom ; prompts cherchés dans `PROMPTS_DIR`, puis `prompts/` de l'agent (`cli.DEFAULT_PROMPTS_DIR`), puis `agent_socle/prompts` (`token_saving_rules`, `odatio_docs`, `db_access`) ; `agent_socle.ihm` : `run_main_step` et journaux, chemins lus à chaque appel (`AGENT_JOURNAL_PATH`, `AGENT_RUN_LOGS_DIR`, défaut sous `AGENT_DATA_DIR`) |
| Délégation (phase 3) | `cli.deleguer(nom, sys.argv[1:])` : `main.py` de `AGENT_<NOM>_DIR` (défaut : `agent-<nom>` à côté de `AGENT_DATA_DIR`) ; `--step review-pr` → agent-revue-pr ; page « Revue de PR » → lien vers 8508 (`AGENT_REVUE_PR_URL`) |
| Revue de PR (phase 3) | `REVIEW_TOOLS` copié (aussi utilisé par l'Assistant) ; `odatio-specific-code-conventions-dev.md` reste à la racine d'agent-scrum-master (aussi lu par le patch, Lancement) et lu sous `AGENT_DATA_DIR` ; base REST de `pr_review` → `agent_socle.bitbucket_rest` (Cucumber : `cucumber_repo_setup`, PR Java 21) |
| Élagage de `main()` (phase 4) | `scripts/elaguer_main.py` : évaluation partielle (`args.step`, drapeaux `is_*`/`jira_only` fixés) des `if` et des `a if C else b` de `main()` ; branche fausse retirée, vraie remontée (seuls `elif`→`if` et l'indentation changent) ; agent-scrum-master élagué de `besoin`/`cadrage-reponses` et délègue ces étapes à agent-integration |
| Intégration (phase 4) | CLI placée dans `AGENT_DATA_DIR` (`os.chdir` au lancement) : `<TICKET>-cadrage.md` et fichiers produits au même endroit qu'avant ; IHM : seul `os.path.exists` du cadrage lu sous `AGENT_DATA_DIR` ; `jira_tools.update_jira_section` reste au socle (générique, testée par `test_jira_publication`) ; `SECTION_TITLE` → `agent_socle.besoin` (lu aussi par Lancement) ; tests qui patchent `PUBLICATION_DIR` : patch aussi sur `agent_socle.publication` |
| Correctif de chemin (phase 4) | consigne Jira de `main()` : `from tools.jira_tools import …` (module parti au socle en phase 1) → `from agent_socle.jira_tools import …` |
| Lancement (phase 5) | CLI et IHM placées dans `AGENT_DATA_DIR` (l'IHM seulement lancée depuis son dépôt ; ses commandes visent son `main.py` par chemin absolu) ; lien du manager `?ticket=…` : scrum-master transmet ticket et contexte au bouton vers 8506, agent-lancement l'applique (même bloc qu'avant) ; réglages du chiffrage dans l'IHM 8506 |
| Socle v0.5.0 (phase 5) | `agent_socle.depots` : copies locales et version d'Odatio (communes Lancement/Cucumber), avec `disable_push`, `cucumber_std_ref`/`names`, `STD_REF_LOG` ; `agent_socle.versions` : `normalize`, `std_refs` ; `publication.record_publication` ; prompt `std_bitbucket_code` au socle (lu aussi par la postmerge) ; `elaguer_main` déduit les drapeaux affectés une fois et les dictionnaires vides constants |
| Tests croisés (phase 5) | tests du socle qui importaient `main` d'agent-scrum-master : `std_bitbucket_code` et `share_knowledge` sur le socle seul ; `inventory_markdown` → scrum-master, `patch_recap` → agent-lancement ; reste `test_skills` (`learn_skill`, Cucumber) et `test_mcp_servers` pour la phase 6 |
| Cucumber (phase 6) | `main.py` et `app.py` d'agent-cucumber : copies élaguées (étapes et pages Cucumber), puis fonctions, constantes et imports inatteignables retirés (gardés s'ils sont lus ou patchés par les tests déplacés) ; agent-scrum-master réduit de même à l'Assistant, Connaissances, Configuration et aux délégations (bloc unique `cli.deleguer` des 4 agents) |
| Dossier de travail de l'IHM Cucumber (phase 6) | lancée depuis son dépôt, elle se place dans `AGENT_DATA_DIR` ; ses commandes gardent `"main.py"` relatif (les tests reconnaissent les lancements ainsi) : c'est celui d'agent-scrum-master, qui délègue à agent-cucumber |
| Socle v0.6.0 (phase 6) | `reglages` : valeurs par défaut des réglages Cucumber de la page Configuration et `agent_runs_tests` (réexportés par leurs modules) ; `verrou` : lecture du verrou pour le lecteur du manager (`holder` copié, prise et libération dans agent-cucumber) ; garde `DeliveryBlocked` autour de `main()` : seulement dans agent-cucumber |
| Manager (phase 7) | worktree `C:gent-manager-savoye-phase7` depuis `origin/main` (branche locale de l'utilisateur intacte) ; repris : assistant, lecture_manager, poller_control (`manager/`), webhook_server.py, mcp_lecture.py, `main.py --step ask`, onglets « Assistant des agents » et « Connaissances partagées » ; liens `?page=` d'agent-scrum-master redirigés vers l'agent du registre (sinon agent-scrum-master renvoie) ; registre modèle : 5 agents (venv d'agent-scrum-master) ; venv du manager : python-dotenv, agent-socle `--no-deps`, pytest (dev) |
| Assistant des agents (phase 7) | code lu : `AGENT_DATA_DIR` et les dépôts des agents (chemins seuls ; le modèle `assistant` inchangé) |
| Compte Claude (phase 7) | reste dans le formulaire global `.env` de la Configuration d'agent-scrum-master (règle `CLAUDE_CONFIG_DIR` de tous les agents) : le déplacer découperait le formulaire |
| Travaux en cours (phase 8, changement voulu) | `agent_socle.taches` : relais détaché (`python -m agent_socle.taches <agent> <id> -- <commande>`, CREATE_NEW_PROCESS_GROUP + DETACHED_PROCESS) qui écrit journal de l'exécution et journal technique ; registre `.tmp/taches/<agent>/<id>.json`, statuts en_cours/terminee/echec/interrompue/arretee ; `ihm.run_main_step` lance puis suit le journal (sortie fournie par un processus simulé : lue comme avant, tests inchangés) ; contexte `.tmp/sessions/<auteur>/<agent>.json` (clés par agent ; Cucumber : projet des onglets simple et avancé, `cs_project` et `cucumber_project` — tous les `PERSISTENT_KEYS` rechargés ensemble changeaient la page d'une nouvelle session) ; panneau en tête des IHM Intégration, Lancement, Cucumber, Revue de PR ; récapitulatif dans le tableau de bord du manager ; `cucumber_lock` inchangé (verrou générique non requis) |
| Référence pytest (main 4e6fba8) | 1 053 passés, 1 ignoré, 0 échec (les 3 échecs connus ne se reproduisent plus) ; suite complète ≈ 30 min |

## 8. Journal d'avancement

| Phase | Dépôt | PR | Résultat |
|---|---|---|---|
| 0 | agent-scrum-master | #52 | cartographie ; 6 dépôts privés créés et clonés sous `C:\` ; pytest 1 053 passés |
| 1 | agent-socle | #1 | socle v0.1.0 (tag) : 22 modules + `git_tools`, `horodatage`, `chemins` ; 140 tests passés |
| 2 | agent-socle | #2 | v0.2.0 (tag) : `projets` (façade + client 8512), `fichiers`, `config_commune` ; 148 tests |
| 2 | agent-projets | #1 | API 8512, IHM 8504 (registre + tests de connexion), CLI ; 7 tests |
| 2 | agent-scrum-master | #54 | socle v0.2.0, tests de connexion déplacés (lien vers 8504) ; pytest 912 passés + 141 au socle = 1 053 (référence), 0 échec |
| 1 | agent-scrum-master | #53 | utilise `agent_socle` ; 11 fichiers de tests partis au socle ; pytest 914 passés + 139 au socle = 1 053 (référence), 0 échec |
| 3 | agent-socle | #3 | v0.3.0 (tag) : `cli`, `ihm`, `bitbucket_rest`, prompts communs ; 156 tests |
| 3 | agent-revue-pr | #1 | `pr_review`, étape `review-pr`, page Revue de PR (8508) ; 16 tests |
| 3 | agent-scrum-master | #56 | socle v0.3.0, `review-pr` délégué à agent-revue-pr, page Revue → lien 8508 ; pytest 899 passés + 13 dans agent-revue-pr = 912, 0 échec (suite 15 min) |
| 4 | agent-socle | #4 | v0.4.0 (tag) : `publication`, `besoin`, `projets.client_name_from_repo`, fonctions communes `cli`/`ihm`, prompts `release_note`/`cucumber_disabled` ; 158 tests |
| 4 | agent-integration | #1 | `integration_tools`, étapes `besoin`/`cadrage-reponses` (main() élagué), onglet Intégration (8505) ; 5 tests |
| 4 | agent-scrum-master | #57 | socle v0.4.0, `besoin`/`cadrage-reponses` délégués à agent-integration (main() élagué par `scripts/elaguer_main.py`), onglet Intégration → lien 8505 ; pytest 898 passés + 1 déplacé = 899, 0 échec |
| 5 | agent-socle | #5 | v0.5.0 (tag) : `depots`, `versions`, aides des étapes dans `cli`, `record_publication` ; 160 tests |
| 5 | agent-lancement | #1 | analysis_publication, estimation, epic_tools, étapes cadrage/refine/epic-cadrage/epic-sync/patch, onglet Lancement (8506) ; 47 tests |
| 5 | agent-scrum-master | #58 | socle v0.5.0, cadrage/refine/epic-cadrage/epic-sync/patch délégués à agent-lancement, onglet Lancement → lien 8506 (ticket du manager transmis) ; pytest 853 passés, 0 échec (tests de Lancement dans agent-lancement : 52) |
| 6 | agent-socle | #6 | v0.6.0 (tag) : `reglages`, `verrou` ; tests qui importaient main sur le socle seul ; 160 tests |
| 6 | agent-cucumber | #1 | 74 modules, prompts Cucumber, jenkins/, 84 fichiers de tests ; main.py et app.py élagués ; 839 passés, 1 ignoré |
| 6 | agent-scrum-master | #59 | socle v0.6.0 ; reste Assistant, Connaissances, Configuration et délégations (main.py 196 lignes, app.py 1 274) ; pytest 17 passés, 0 échec |
| 7 | agent-manager-savoye | #9 | Assistant des agents, Connaissances, webhook, poller, lecteur MCP, CLI ask, redirection des liens, registre ; pytest 100, unittest 90 |
| 7 | agent-socle | #7 | tests de lien_manager déplacés ; 162 tests |
| 7 | agent-scrum-master | #60 | Assistant, Connaissances, webhook, poller, lecteur MCP partis au manager ; `ask` délégué ; reste Configuration et compatibilité ; pytest 5 passés |
| 8 | agent-socle | #8 | v0.7.0 (tag) : `taches` (relais détaché, registre, contexte), `run_main_step` détaché et suivi, panneau « Travaux en cours » ; 170 tests |
| 8 | agent-integration, agent-lancement, agent-revue-pr | #3, #3, #2 | panneau en tête de page, contexte rechargé ; 5, 52, 16 tests |
| 8 | agent-cucumber | #2 | panneau, contexte (projet simple et avancé), modèle secrets.toml.example |
| 8 | agent-manager-savoye | #10 | récapitulatif des travaux en cours (tableau de bord) ; pytest 101 |
| 8 | agent-scrum-master | #61 | README et CONTEXTE-DEV (découpage), socle v0.7.0 ; vérification à blanc : tâche de 30 s suivie après fermeture et réouverture ; 5 passed in 7.78s |
