import glob
import json
import os
import shutil
import subprocess
import sys
import time
import streamlit as st
import logging

from dotenv import dotenv_values, load_dotenv

from agent_socle import config_commune
from agent_socle import auth_tools, env_tools, lien_manager
from agent_socle import knowledge_tools
from urllib.parse import urlencode
from agent_socle.jira_tools import get_jira_issue_summary
from agent_socle.db_tools import DB_TYPES
from tools import assistant
# Lancement de main.py et journal : socle (commun aux IHM des agents)
from agent_socle.ihm import interruption_cause, run_main_step
from agent_socle import skills
from agent_socle import mcp_servers
from agent_socle.memory_tools import (load_ticket_memory,
    get_all_memories,
    clear_memory,
    delete_ticket_memory,
    import_existing_deliverables)
from agent_socle import projets, reglages

# Pleine largeur par défaut (tableaux, revues de PR, état des tests) ; APP_LAYOUT=centered pour une colonne centrale
st.set_page_config(page_title="Scrum Master IA Dashboard",
                   layout="centered" if (os.getenv("APP_LAYOUT") or dotenv_values(os.path.join(
                       os.path.dirname(os.path.abspath(__file__)), ".env")).get("APP_LAYOUT") or "").strip().lower()
                   == "centered" else "wide")


def env_int(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, "").strip() or default)
    except ValueError:
        return default


def claude_config_dirs() -> list:
    """Dossiers de configuration Claude Code du poste (un par compte) : ~/.claude et ~/.claude-*."""
    home = os.path.expanduser("~")
    dirs = [os.path.join(home, ".claude")] + sorted(glob.glob(os.path.join(home, ".claude-*")))
    return [d for d in dirs if os.path.isdir(d)]


@st.cache_data(ttl=300, show_spinner=False)
def claude_account(config_dir: str) -> str:
    """Compte connecté dans ce dossier de configuration (`claude auth status`), ou '' s'il est inconnu."""
    env = os.environ.copy()
    env.pop("CLAUDE_CONFIG_DIR", None)
    if config_dir:
        env["CLAUDE_CONFIG_DIR"] = os.path.expanduser(config_dir)
    try:
        result = subprocess.run([shutil.which("claude") or "claude", "auth", "status"], capture_output=True,
                                text=True, encoding="utf-8", errors="replace", env=env, timeout=20,
                                shell=(os.name == "nt"))
        status = json.loads(result.stdout)
    except (OSError, subprocess.SubprocessError, ValueError):
        return ""
    if not status.get("loggedIn"):
        return "non connecté"
    return " · ".join(v for v in (status.get("email"), status.get("orgName")) if v)


def secret_input(name: str, label: str = "", **kwargs) -> str:
    """Champ d'un secret du .env : jamais renvoyé au navigateur quand la connexion OIDC est active (auth_tools)."""
    return auth_tools.secret_field(st, name, label, **kwargs)


ENV_PATH = ".env"
if not os.path.exists(ENV_PATH):
    open(ENV_PATH, "w", encoding="utf-8").close()

# override=True : Streamlit ré-exécute ce script à chaque interaction ; on recharge donc la
# configuration enregistrée (onglet ⚙️) au lieu de garder les valeurs du démarrage.
# CLAUDE_CONFIG_DIR vide dans le .env : on garde celui du shell de lancement (mémorisé au premier passage).
os.environ.setdefault("AGENT_SHELL_CLAUDE_CONFIG_DIR", os.environ.get("CLAUDE_CONFIG_DIR", ""))
# Lignes du .env en défaut : un message clair au démarrage (numéros de ligne) au lieu des avertissements bruts
logging.getLogger("dotenv.main").setLevel(logging.ERROR)
load_dotenv(ENV_PATH, override=True)
# Lancé par agent-manager-savoye : ses accès et secrets l'emportent sur le .env (tools/config_commune.py)
config_commune.reappliquer()
if not os.getenv("CLAUDE_CONFIG_DIR", "").strip():
    os.environ["CLAUDE_CONFIG_DIR"] = os.environ["AGENT_SHELL_CLAUDE_CONFIG_DIR"]

# Connexion OIDC (AGENT_AUTH) : rien n'est affiché ni exécuté avant elle
CONNECTED = auth_tools.require_login(st)

st.title("🤖 Tableau de bord - Agent Scrum Master")


# Navigation : seule la page choisie est exécutée (st.tabs exécutait toutes les pages à chaque interaction, avec
# leurs appels réseau et les fragments à rafraîchissement automatique de l'onglet Cucumber)
PAGES = {"integration": "🧩 Intégration", "lancement": "🚀 Lancement", "cucumber": "🥒 Cucumber",
         "cucumber-avance": "🥒 Cucumber (avancé)", "revue": "🔍 Revue de PR", "assistant": "💬 Assistant",
         "connaissances": "📚 Connaissances partagées", "configuration": "⚙️ Configuration (.env)"}
NAV_KEY = "nav_page"
# Réglages saisis sur une page et gardés quand on en change (Streamlit efface l'état d'un widget non affiché) :
# seulement des widgets sans valeur par défaut passée en paramètre (sinon avertissement de Streamlit)
PERSISTENT_KEYS = ("cucumber_project", "cucumber_repo", "cucumber_client_name", "cucumber_db_schema",
                   "cucumber_application", "cucumber_std_repo", "cucumber_source_schema",
                   "cs_project", "cs_repo", "cs_client", "cs_application", "cs_source_schema", "cs_std_repo",
                   "integ_selected") + tuple(f"{prefix}{name}" for prefix in ("", "integ_") for name in (
                       "open_epics_only", "search_links", "search_project", "search_team", "search_statuses", "search_labels"))


def keep_page_state() -> None:
    """Valeurs des réglages des autres pages conservées (réaffectées avant tout widget, comme le préconise Streamlit)."""
    for key in PERSISTENT_KEYS:
        if key in st.session_state:
            st.session_state[key] = st.session_state[key]


def page_changed() -> None:
    """Page désélectionnée (clic sur la page active) : la page précédente reste ; l'adresse garde la page choisie."""
    page = st.session_state.get(NAV_KEY)
    if page not in PAGES:
        page = st.session_state.get("nav_last") or next(iter(PAGES))
        st.session_state[NAV_KEY] = page
    st.session_state["nav_last"] = page
    st.query_params["page"] = page


def render_navigation() -> str:
    """Choix de la page (adresse ?page=… reprise au rechargement du navigateur). Renvoie la page à exécuter."""
    keep_page_state()
    if st.session_state.get(NAV_KEY) not in PAGES:
        wanted = st.query_params.get("page", "")
        st.session_state[NAV_KEY] = wanted if wanted in PAGES else next(iter(PAGES))
    st.session_state["nav_last"] = st.session_state[NAV_KEY]
    st.segmented_control("Page", list(PAGES), key=NAV_KEY, format_func=PAGES.get, on_change=page_changed,
                         label_visibility="collapsed", width="stretch")
    return st.session_state.get(NAV_KEY) or st.session_state["nav_last"]


@st.cache_data(ttl=60, show_spinner=False)
def agent_version() -> str:
    """Version de l'agent : branche et commit du dépôt (« modifié » si des fichiers suivis ne sont pas commités)."""
    root = os.path.dirname(os.path.abspath(__file__))
    git = lambda *args: subprocess.run(["git", "-C", root, *args], capture_output=True, text=True,  # noqa: E731
                                       encoding="utf-8", errors="replace").stdout.strip()
    try:
        commit = git("log", "-1", "--format=%h du %cd", "--date=format:%d/%m/%Y %H:%M")
        branch = git("rev-parse", "--abbrev-ref", "HEAD")
        dirty = bool(git("status", "--porcelain", "--untracked-files=no"))
    except OSError:
        return ""
    return f"Version de l'agent : {commit or 'inconnue'}" + (f" ({branch})" if branch else "") +         (" — modifications locales non commitées" if dirty else "")


if CONNECTED:
    with st.container(horizontal=True, vertical_alignment="center"):
        st.caption(f"{agent_version()} — connecté : {auth_tools.label(CONNECTED)}")
        st.button("Se déconnecter", on_click=st.logout, icon=":material/logout:", key="auth_logout")
else:
    st.caption(agent_version())

env_problems = env_tools.check_env(ENV_PATH)
if env_problems:
    with st.container(border=True):
        st.warning(env_tools.check_line(env_problems), icon=":material/warning:")
        if st.button("Neutraliser les lignes invalides", icon=":material/comment:", key="env_neutralize",
                     help="Met ces lignes en commentaire (rien n'est supprimé) ; copie de l'ancien fichier : .env.bak"):
            st.success(f"{env_tools.neutralize_invalid_lines(ENV_PATH)} ligne(s) mise(s) en commentaire ; copie : "
                       f".env.bak.")
            st.rerun()

def apply_manager_link() -> None:
    """
    Lien prérempli d'agent-manager-savoye (tools/lien_manager.py), appliqué une fois par session : page, ticket posé
    dans « Lancement », client de l'onglet « Cucumber », question préremplie de l'« Assistant », résumé affiché.
    Rien n'est lancé.
    """
    link = lien_manager.lire(st.query_params.to_dict(), PAGES)
    if not link or st.session_state.get("manager_link_signature") == lien_manager.signature(link):
        return
    st.session_state["manager_link_signature"] = lien_manager.signature(link)
    st.session_state["manager_link"] = link
    # Page demandée ; à défaut, celle de ce qui est transmis (question → Assistant, ticket → Lancement)
    page = link.get("page") or ("assistant" if link.get("question") else "lancement" if link.get("ticket") else "")
    if page:
        st.session_state[NAV_KEY] = page
    ticket = link.get("ticket")
    if ticket:
        title = get_jira_issue_summary(ticket)
        project = lien_manager.projet_jira(ticket)
        st.session_state.update(jira_tickets=[ticket], jira_titles={ticket: title} if title else {},
                                jira_context={}, selected_index=0, selected_jira_project=project,
                                workflow_step="INIT", search_project=project)
        st.session_state.pop("ticket_selectbox", None)
    client = lien_manager.projet_connu(link.get("projet", ""),
                                       projets.known_projects(projets.CUCUMBER_CACHE))
    if client:
        st.session_state["cs_project"] = client
        st.session_state.pop("cs_repo", None)        # réglages du client rechargés (load_simple_project)
    if link.get("question"):
        st.session_state["manager_question"] = link["question"]


def render_manager_link() -> None:
    """Bandeau du lien du manager : ce qui a été transmis, masquable."""
    link = st.session_state.get("manager_link")
    if not link:
        return
    with st.container(border=True):
        details = " · ".join(x for x in (link.get("ticket"), link.get("projet")) if x)
        st.markdown("**Ouvert depuis le manager**" + (f" — {details}" if details else ""))
        if link.get("contexte"):
            st.caption(link["contexte"])
        if link.get("ticket") and st.session_state.get(NAV_KEY) == "lancement":
            st.caption(f"Ticket {link['ticket']} posé dans « Lancement » : rien n'est lancé sans toi.")
        if st.button("Masquer", key="manager_link_hide", type="tertiary"):
            st.session_state.pop("manager_link", None)
            st.rerun()


apply_manager_link()
PAGE = render_navigation()
render_manager_link()

# Initialisation de l'état
if "workflow_step" not in st.session_state:
    st.session_state["workflow_step"] = "INIT"

# ---------------------------------------------------------
# ONGLET 1 : LANCEMENT & RECHERCHE PARAMÉTRABLE
# ---------------------------------------------------------
if PAGE == "lancement":
    # Lancement : agent-lancement (IHM 8506) ; le ticket transmis par le manager lui est passé
    lien = st.session_state.get("manager_link") or {}
    params = {k: lien[k] for k in ("ticket", "contexte") if lien.get(k)}
    st.link_button("Lancement", (os.getenv("AGENT_LANCEMENT_URL", "").strip() or "http://localhost:8506")
                   + ("/?" + urlencode({"source": "manager", **params}) if params else ""),
                   icon=":material/rocket_launch:")


# ---------------------------------------------------------
# ONGLET 2 : INTÉGRATION — BESOIN REFORMULÉ ET QUESTIONS OUVERTES
# ---------------------------------------------------------


if PAGE == "integration":
    # Intégration : agent-integration (IHM 8505)
    st.link_button("Intégration", os.getenv("AGENT_INTEGRATION_URL", "").strip() or "http://localhost:8505",
                   icon=":material/integration_instructions:")


# ---------------------------------------------------------
# ONGLET CUCUMBER : PARCOURS EN 7 ÉTAPES, DU CHOIX DES TÂCHES AU PATCH VERT
# ---------------------------------------------------------
if PAGE in ("cucumber", "cucumber-avance"):
    # Cucumber : agent-cucumber (IHM 8507) ; page et projet transmis par le manager lui sont passés
    lien = st.session_state.get("manager_link") or {}
    params = {"source": "manager", "page": PAGE, **({"projet": lien["projet"]} if lien.get("projet") else {})}
    st.link_button("Cucumber", (os.getenv("AGENT_CUCUMBER_URL", "").strip() or "http://localhost:8507")
                   + ("/?" + urlencode(params) if lien else ""), icon=":material/science:")


def render_knowledge_outbox() -> None:
    """
    Documents en file locale (partage indisponible lors du dépôt) : nombre affiché, nouvel essai au premier
    affichage de la session puis à la demande.
    """
    if not knowledge_tools.enabled():
        return
    retry = not st.session_state.get("knowledge_outbox_tried") and knowledge_tools.outbox_count()
    with st.container(horizontal=True, vertical_alignment="center"):
        retry = st.button("Déposer les documents en attente", icon=":material/cloud_upload:",
                          disabled=not knowledge_tools.outbox_count()) or retry
        pending_caption = st.empty()
    if retry:
        st.session_state["knowledge_outbox_tried"] = True
        with st.spinner("Dépôt des documents en attente…"):
            flushed = knowledge_tools.flush_outbox()
        for message in flushed["messages"]:
            st.caption(message)
    pending = knowledge_tools.outbox_count()
    pending_caption.caption(f":orange[{pending} document(s) en attente de dépôt] (partage indisponible lors de "
                            f"l'analyse, file locale .tmp/knowledge_outbox)" if pending else
                            "Aucun document en attente de dépôt.")


# ---------------------------------------------------------
# ONGLET REVUE DE PR : RELIRE LES PULL REQUESTS DES COLLABORATEURS (BITBUCKET)
# ---------------------------------------------------------


if PAGE == "revue":
    # Revue de PR : agent-revue-pr (IHM 8508)
    st.link_button("Revue de PR", os.getenv("AGENT_REVUE_PR_URL", "").strip() or "http://localhost:8508",
                   icon=":material/rate_review:")


# ---------------------------------------------------------
# ONGLET ASSISTANT : QUESTIONS LIBRES SUR LES FONCTIONNALITÉS ET LES RÉSULTATS
# ---------------------------------------------------------
ASSISTANT_EXAMPLES = ["Pourquoi les tests Cucumber d'Apparel encore en échec échouent-ils ?",
                      "Que fait le bouton « Lancer » de l'onglet Cucumber ?",
                      "Qu'a corrigé l'agent lors de la dernière correction ?",
                      "Que contiennent les modifications à appliquer actuelles ?"]


def ask_assistant(question: str) -> None:
    """Question ajoutée à la conversation, réponse de l'agent (lecture seule) ajoutée à son tour."""
    messages = assistant.load_history()
    messages.append({"role": "user", "content": question, "date": time.strftime("%Y-%m-%dT%H:%M:%S")})
    assistant.save_history(messages)
    answer_file = os.path.join(assistant.ROOT, "reponse.md")
    if os.path.exists(answer_file):
        os.remove(answer_file)
    with st.spinner("L'assistant cherche dans l'application et ses résultats…"):
        code = run_main_step([sys.executable, "main.py", "--step", "ask", "--question", question,
                              "--answer-file", answer_file])
    if code == 0 and os.path.exists(answer_file):
        with open(answer_file, encoding="utf-8") as f:
            answer = f.read()
    else:
        answer = f"⚠️ Pas de réponse : {interruption_cause(code)}."
    messages.append({"role": "assistant", "content": answer, "date": time.strftime("%Y-%m-%dT%H:%M:%S")})
    assistant.save_history(messages)


def render_assistant_tab() -> None:
    st.markdown("### Poser une question à l'agent")
    st.caption("Sur ce que fait l'application (onglets, boutons, réglages) ou sur ses résultats (tests en échec et "
               "pourquoi, corrections faites, patchs, runs Jenkins importés, revues de PR, consommation). L'assistant "
               "lit le code de l'application et ses résultats enregistrés ; il ne modifie et ne lance rien.")
    messages = assistant.load_history()
    with st.container(horizontal=True):
        if st.button("Nouvelle conversation", icon=":material/add_comment:", disabled=not messages,
                     key="assistant_clear"):
            assistant.clear_history()
            st.rerun()
    if not messages:
        st.caption("Exemples :")
        with st.container(horizontal=True):
            for index, example in enumerate(ASSISTANT_EXAMPLES):
                if st.button(example, type="tertiary", key=f"assistant_example_{index}"):
                    st.session_state["assistant_pending"] = example
    prepared = st.session_state.get("manager_question")
    if prepared:
        with st.container(border=True):
            st.caption("Question préparée par le manager (envoyée seulement si tu cliques) :")
            st.markdown(prepared)
            with st.container(horizontal=True):
                if st.button("Envoyer cette question", type="primary", key="manager_question_send"):
                    st.session_state["assistant_pending"] = st.session_state.pop("manager_question")
                if st.button("Ignorer", key="manager_question_drop"):
                    st.session_state.pop("manager_question", None)
                    st.rerun()
    for message in messages:
        with st.chat_message(message["role"]):
            st.markdown(message["content"])
    question = st.chat_input("Pose ta question…", key="assistant_input") or st.session_state.pop("assistant_pending", "")
    if question:
        with st.chat_message("user"):
            st.markdown(question)
        ask_assistant(question)
        st.rerun()


if PAGE == "assistant":
    render_assistant_tab()


# ---------------------------------------------------------
# ONGLET CONNAISSANCES PARTAGÉES : RECHERCHE, APERÇU, STATUT, IMPORT
# ---------------------------------------------------------
KNOWLEDGE_ALL_TYPES = "Tous les types"


def open_document(path: str) -> None:
    """Ouvre un document avec l'application associée du poste (l'IHM tourne sur le poste de l'utilisateur)."""
    if os.name == "nt":
        os.startfile(path)
    else:
        subprocess.Popen(["open" if sys.platform == "darwin" else "xdg-open", path])


def render_knowledge_import() -> None:
    """Import ponctuel de .md existants (A_REVOIR.md, AUDIT.md, COUVERTURE.md...) : projet, type et tickets choisis."""
    with st.expander("Importer des analyses Markdown existantes", icon=":material/upload_file:"):
        with st.form("knowledge_import", clear_on_submit=True):
            uploaded = st.file_uploader("Fichiers .md", type=["md"], accept_multiple_files=True)
            with st.container(horizontal=True):
                import_project = st.text_input("Projet Jira", placeholder="PROJ")
                import_client = st.text_input("Client (dossier, facultatif)",
                                              help="Dossier du document : le client s'il est renseigné, sinon le "
                                                   "projet Jira.")
                import_type = st.selectbox("Type", knowledge_tools.KNOWLEDGE_TYPES,
                                           index=knowledge_tools.KNOWLEDGE_TYPES.index("autre"))
            import_tickets = st.text_input("Tickets", placeholder="PROJ-1, PROJ-2")
            import_subject = st.text_input("Sujet", placeholder="Vide = nom du fichier")
            import_clicked = st.form_submit_button("Importer", icon=":material/upload:")
        if not import_clicked:
            return
        if not uploaded or not import_project.strip():
            st.error("Choisissez au moins un fichier et renseignez le projet Jira.", icon=":material/error:")
            return
        for file in uploaded:
            result = knowledge_tools.import_text(
                file.name, file.getvalue().decode("utf-8-sig", errors="replace"), import_project.strip().upper(),
                import_type, [t for t in import_tickets.split(",")], import_client.strip(), import_subject.strip())
            ok = result["statut"] in (knowledge_tools.PUBLISHED, knowledge_tools.QUEUED)
            (st.success if ok else st.error)(f"{file.name} : {result['message']}",
                                             icon=":material/check_circle:" if ok else ":material/error:")
        st.session_state.pop("knowledge_index", None)


def render_knowledge_status(doc: dict, meta: dict, documents: list) -> None:
    """Action « marquer obsolète / remplacé par » : en-tête seul modifié, par écriture atomique."""
    folder = doc["chemin"].rsplit("/", 1)[0]
    # Documents du même projet et du même type d'abord
    others = sorted((d["chemin"] for d in documents if d["chemin"] != doc["chemin"]),
                    key=lambda p: (p.rsplit("/", 1)[0] != folder, p))
    current = meta.get("remplace_par", "")
    with st.form(f"knowledge_status_{doc['chemin']}"):
        new_status = st.segmented_control("Statut", knowledge_tools.STATUSES,
                                          default=meta.get("statut", knowledge_tools.STATUS_ACTIVE), required=True)
        replacement = st.selectbox("Remplacé par (statut « remplacé »)", [""] + others,
                                   index=([""] + others).index(current) if current in others else 0)
        save_clicked = st.form_submit_button("Enregistrer le statut", icon=":material/save:")
    if save_clicked:
        result = knowledge_tools.set_status(doc["chemin"], new_status or knowledge_tools.STATUS_ACTIVE, replacement)
        if result["ok"]:
            st.session_state["knowledge_index"] = knowledge_tools.load_index()
            st.toast(result["message"], icon=":material/check_circle:")
            st.rerun()
        st.error(result["message"], icon=":material/error:")


def render_knowledge_document(doc: dict, documents: list) -> None:
    """Aperçu d'un document : en-tête, résumé, corps ; ouverture, téléchargement et changement de statut."""
    try:
        path = knowledge_tools.absolute_path(doc["chemin"])
        meta, body = knowledge_tools.read_document(path)
    except (ValueError, OSError, UnicodeDecodeError) as e:
        st.error(f"Document illisible : {e}", icon=":material/error:")
        return
    with st.container(border=True):
        st.markdown(f"**{meta.get('sujet') or doc['chemin']}**")
        status = meta.get("statut", knowledge_tools.STATUS_ACTIVE)
        if meta.get("remplace_par"):
            status += f" par {meta['remplace_par']}"
        st.caption(f"`{doc['chemin']}` · {meta.get('type', '?')} · {str(meta.get('date', '?')).replace('T', ' ')} · "
                   f"{meta.get('auteur', '?')} · statut : {status} · tickets : "
                   f"{', '.join(meta.get('tickets') or []) or 'aucun'} · agent {meta.get('version_agent', '?')}")
        if meta.get("resume"):
            st.markdown(meta["resume"])
        with st.container(horizontal=True):
            if st.button("Ouvrir", icon=":material/open_in_new:", key="knowledge_open"):
                try:
                    open_document(path)
                except OSError as e:
                    st.error(f"Ouverture impossible : {e}", icon=":material/error:")
            st.download_button("Télécharger", data=knowledge_tools.render_document(meta, body),
                               file_name=os.path.basename(path), mime="text/markdown", icon=":material/download:",
                               key="knowledge_download")
        with st.container(height=420):
            st.markdown(body)
    render_knowledge_status(doc, meta, documents)


def render_skills() -> None:
    """Compétences acquises par l'agent (auto-amélioration) : liste, lecture, désactivation, suppression."""
    all_skills = skills.load_skills(include_disabled=True)
    active = sum(1 for k in all_skills if k["actif"])
    with st.expander(f"🧠 Compétences acquises par l'agent : {len(all_skills)} ({active} actives)",
                     expanded=False):
        st.caption("Après une tâche complexe réussie et PROUVÉE (test rendu vert sur Jenkins, initialisation repassée), "
                   "l'agent rédige la procédure qui lui a permis de réussir. Il la relit avant les tâches semblables "
                   f"suivantes. Rangées dans : {skills.skills_dir()}" + (" (partagé avec l'équipe)" if
                                                                      os.getenv("AGENT_KNOWLEDGE_DIR", "").strip()
                                                                      else " (local)") + "."
                   + ("" if skills.enabled() else " ⚠️ Apprentissage désactivé (SKILLS_AUTO=0)."))
        if not all_skills:
            st.caption("Aucune compétence pour l'instant : elles apparaissent après les premières corrections "
                       "réussies.")
            return
        for skill in sorted(all_skills, key=lambda k: (not k["actif"], -k["utilisations"], k.get("nom", ""))):
            with st.container(border=True):
                st.markdown(f"**{skill.get('nom', skill['fichier'])}** — {skill.get('description', '')}"
                            + ("" if skill["actif"] else " :gray[(désactivée)]"))
                st.caption(f"{skills.DOMAINS.get(skill.get('domaine', ''), skill.get('domaine', ''))} · utilisée "
                           f"{skill['utilisations']} fois · mise à jour le {skill.get('maj_le', '')[:16].replace('T', ' ')}"
                           f" · origine : {skill.get('origine', '')}")
                with st.container(horizontal=True):
                    show = st.toggle("Voir la procédure", key=f"skill_show_{skill['fichier']}")
                    if st.button("Réactiver" if not skill["actif"] else "Désactiver", type="tertiary",
                                 key=f"skill_toggle_{skill['fichier']}"):
                        skills.set_active(skill["chemin"], not skill["actif"])
                        st.rerun()
                    if st.button("Supprimer", type="tertiary", key=f"skill_delete_{skill['fichier']}"):
                        os.remove(skill["chemin"])
                        st.rerun()
                if show:
                    st.markdown(skill["corps"])


def render_knowledge_tab() -> None:
    """Onglet 📚 : état du répertoire partagé, recherche, aperçu, statut et import."""
    st.subheader("Connaissances partagées")
    st.caption("Analyses déposées par l'agent dans le répertoire partagé (AGENT_KNOWLEDGE_DIR), consultées avant "
               "chaque nouvelle analyse comme des données à vérifier. Tout utilisateur du partage lit tous les "
               "documents.")
    render_skills()
    if not knowledge_tools.enabled():
        st.info("AGENT_KNOWLEDGE_DIR est vide : renseignez le répertoire partagé dans l'onglet ⚙️ Configuration.",
                icon=":material/info:")
        return
    state_box = st.container()
    render_knowledge_outbox()
    render_knowledge_import()

    with st.form("knowledge_search"):
        with st.container(horizontal=True):
            search_project = st.text_input("Projet ou client", key="knowledge_project")
            search_type = st.selectbox("Type", [KNOWLEDGE_ALL_TYPES, *knowledge_tools.KNOWLEDGE_TYPES],
                                       key="knowledge_type")
            search_ticket = st.text_input("Ticket", key="knowledge_ticket")
            search_keyword = st.text_input("Mot-clé", key="knowledge_keyword")
        include_obsolete = st.checkbox("Inclure les documents obsolètes et remplacés", key="knowledge_obsolete")
        search_clicked = st.form_submit_button("Rechercher", icon=":material/search:")
    # Le partage n'est lu qu'à la demande : tous les onglets s'exécutent à chaque interaction
    if search_clicked:
        with st.spinner("Lecture des en-têtes du répertoire partagé…"):
            st.session_state["knowledge_index"] = knowledge_tools.load_index()
    index = st.session_state.get("knowledge_index")

    with state_box, st.container(horizontal=True):
        st.metric("Joignable", "non vérifié" if index is None else ("oui" if index["joignable"] else
                                                                    "non (dernier index connu)"), border=True)
        st.metric("Documents indexés", "—" if index is None else len(index["documents"]), border=True)
        st.metric("En attente de dépôt", knowledge_tools.outbox_count(), border=True)
    state_box.caption(f"Répertoire : {knowledge_tools.knowledge_dir()}")

    if index is None:
        st.caption("Lancez une recherche (critères vides : tous les documents actifs).")
        return
    documents = knowledge_tools.search_documents(
        index["documents"], search_project, "" if search_type == KNOWLEDGE_ALL_TYPES else search_type,
        search_ticket, search_keyword, include_obsolete)
    if not documents:
        st.info("Aucun document ne correspond à ces critères.", icon=":material/search_off:")
        return
    rows = [{"Date": str(d["meta"].get("date", "")).replace("T", " "), "Projet": d["chemin"].split("/", 1)[0],
             "Type": d["meta"].get("type", ""), "Sujet": d["meta"].get("sujet", ""),
             "Tickets": ", ".join(d["meta"].get("tickets") or []), "Auteur": d["meta"].get("auteur", ""),
             "Statut": d["meta"].get("statut", "")} for d in documents]
    st.caption(f"{len(documents)} document(s). Sélectionnez une ligne pour l'aperçu.")
    table = st.dataframe(rows, hide_index=True, on_select="rerun", selection_mode="single-row",
                         key="knowledge_table")
    selected = table.selection.rows
    if selected and selected[0] < len(documents):
        render_knowledge_document(documents[selected[0]], index["documents"])


if PAGE == "connaissances":
    render_knowledge_tab()


if PAGE == "configuration":
    st.subheader("🛠️ Configuration globale du fichier .env")
    st.caption("Chiffrage des analyses par projet : réglages dans l'IHM Lancement (8506).")
    st.caption("Modifiez l'ensemble des variables d'environnement utilisées par l'application. "
               "Le fichier .env.example du projet donne les valeurs usuelles.")
    if config_commune.gerees():
        st.info("Lancé par le manager : ces variables viennent de sa configuration commune et se modifient dans le "
                "manager (onglet Configuration › Accès) ; elles ne sont pas réécrites dans le .env : "
                + ", ".join(config_commune.gerees()) + ".", icon=":material/hub:")

    with st.form("env_form_global"):
        # Valeurs enregistrées dans le .env à la validation du formulaire
        env_values = {}

        st.markdown("#### 📌 Identifiants Jira")
        env_values["JIRA_DOMAIN"] = st.text_input("JIRA_DOMAIN", value=os.getenv("JIRA_DOMAIN", ""),
                                                  help="Ex: monentreprise.atlassian.net")
        env_values["JIRA_EMAIL"] = st.text_input("JIRA_EMAIL", value=os.getenv("JIRA_EMAIL", ""))
        env_values["JIRA_API_TOKEN"] = secret_input("JIRA_API_TOKEN",
                                                     help="À créer sur https://id.atlassian.com/manage-profile/security/api-tokens")
        env_values["JIRA_PROJECT_KEY"] = st.text_input("JIRA_PROJECT_KEY (Par défaut)", value=os.getenv("JIRA_PROJECT_KEY", ""))
        with st.container(horizontal=True):
            env_values["JIRA_READ_EMAIL"] = st.text_input(
                "JIRA_READ_EMAIL (facultatif)", value=os.getenv("JIRA_READ_EMAIL", ""),
                help="Compte Jira en lecture seule transmis à l'agent pendant l'analyse. Vide : l'agent n'a aucun "
                     "jeton Jira pendant l'analyse (le contexte du ticket lui est fourni dans le prompt).")
            env_values["JIRA_READ_API_TOKEN"] = secret_input(
                "JIRA_READ_API_TOKEN", help="Jeton du compte JIRA_READ_EMAIL, sans droit d'écriture.")

        st.markdown("---")
        st.markdown("#### 🔎 Recherche Jira (valeurs par défaut de l'onglet Lancement)")
        with st.container(horizontal=True):
            env_values["JIRA_SEARCH_TEAM"] = st.text_input(
                "JIRA_SEARCH_TEAM", value=os.getenv("JIRA_SEARCH_TEAM", ""),
                help="Équipe / composant pré-rempli dans la recherche (composant Jira ou équipe Atlassian).")
            env_values["JIRA_SEARCH_STATUSES"] = st.text_input(
                "JIRA_SEARCH_STATUSES", value=os.getenv("JIRA_SEARCH_STATUSES", ""),
                help="Statuts pré-remplis, séparés par une virgule. Ex: Analysée, Analysé")
            env_values["JIRA_SEARCH_LABELS"] = st.text_input(
                "JIRA_SEARCH_LABELS", value=os.getenv("JIRA_SEARCH_LABELS", ""),
                help="Labels pré-remplis, séparés par une virgule")
        with st.container(horizontal=True):
            env_values["JIRA_OPEN_EPICS_TEAM"] = st.text_input(
                "JIRA_OPEN_EPICS_TEAM", value=os.getenv("JIRA_OPEN_EPICS_TEAM", ""),
                help="Équipe Atlassian de la recherche rapide « Épopées ouvertes de mon équipe ». Ex: oda_Prenom_N")
            env_values["JIRA_OPEN_EPICS_STATUS"] = st.text_input(
                "JIRA_OPEN_EPICS_STATUS", value=os.getenv("JIRA_OPEN_EPICS_STATUS", ""),
                help="Statut des épopées de cette recherche rapide. Ex: Ouvert")
        with st.container(horizontal=True):
            env_values["JIRA_APPLICATION_FIELD"] = st.text_input(
                "JIRA_APPLICATION_FIELD", value=os.getenv("JIRA_APPLICATION_FIELD", ""),
                help="Champ Jira « Projet / Application » (ex. customfield_10268). Onglet Intégration : l'agent "
                     "consulte tous les tickets ayant la même valeur que le ticket (ex. APPLI). Vide = désactivé.")
            application_max = st.number_input(
                "JIRA_APPLICATION_MAX_TICKETS", min_value=0, step=50,
                value=env_int("JIRA_APPLICATION_MAX_TICKETS", 300),
                help="Nombre maximal de tickets de référence (les plus récemment mis à jour).")
        env_values["JIRA_APPLICATION_MAX_TICKETS"] = str(int(application_max))

        st.markdown("---")
        st.markdown("#### 🎫 Tickets Jira créés par l'agent (tâches d'épopée)")
        with st.container(horizontal=True):
            env_values["POSTMERGE_JIRA_COMPONENT"] = st.text_input(
                "POSTMERGE_JIRA_COMPONENT", value=os.getenv("POSTMERGE_JIRA_COMPONENT", ""),
                help="Composant Jira ou équipe Atlassian posé sur les tâches créées sous une épopée. Vide = aucun.")
            env_values["JIRA_EPIC_TASK_ISSUE_TYPE"] = st.text_input(
                "JIRA_EPIC_TASK_ISSUE_TYPE", value=os.getenv("JIRA_EPIC_TASK_ISSUE_TYPE", ""),
                help="Type des tâches créées sous une épopée. Vide = premier type existant parmi Tâche, Task, "
                     "Development, Story.")
            env_values["JIRA_TEAM_FIELD"] = st.text_input(
                "JIRA_TEAM_FIELD", value=os.getenv("JIRA_TEAM_FIELD", "customfield_10001"),
                help="Champ Jira de l'équipe Atlassian (Team).")

        st.markdown("---")
        st.markdown("#### 🐙 Connexion Bitbucket / KISCloud")
        env_values["BITBUCKET_URL"] = st.text_input("BITBUCKET_URL", value=os.getenv("BITBUCKET_URL", ""),
                                                    help="Ex: https://git.kiscloud.io")
        env_values["BITBUCKET_HTTP_TOKEN"] = secret_input("BITBUCKET_HTTP_TOKEN",
                                                           help="Jeton d'accès HTTP personnel Bitbucket (lecture des dépôts).")
        env_values["BITBUCKET_WRITE_TOKEN"] = secret_input(
            "BITBUCKET_WRITE_TOKEN", help="Jeton réservé aux écritures de l'onglet « Cucumber » sur le dépôt "
                                          "<client>-cucumber (création du dépôt, branche de la version, pushes). "
                                          "BITBUCKET_HTTP_TOKEN reste en lecture.")
        env_values["REVIEW_PROJECTS"] = st.text_input(
            "REVIEW_PROJECTS", value=os.getenv("REVIEW_PROJECTS", ""), placeholder="OXY, APPAREL",
            help="Projets Bitbucket proposés dans la vue « Par projet » de l'onglet Revue de PR (toutes leurs PR "
                 "ouvertes).").strip().upper()

        st.markdown("---")
        st.markdown("#### 🏗️ Connexion Jenkins (Cucumber / Tirs de tests)")
        env_values["JENKINS_URL"] = st.text_input("JENKINS_URL", value=os.getenv("JENKINS_URL", ""),
                                                  help="Ex: https://jenkins.kiscloud.io")
        env_values["JENKINS_USER"] = st.text_input("JENKINS_USER", value=os.getenv("JENKINS_USER", ""))
        env_values["JENKINS_API_TOKEN"] = secret_input("JENKINS_API_TOKEN")
        with st.container(horizontal=True):
            env_values["MAVEN_REPOSITORY_URL"] = st.text_input(
                "MAVEN_REPOSITORY_URL", value=os.getenv("MAVEN_REPOSITORY_URL", ""),
                help="Dépôt Maven où est publié l'artefact monté par le job (maven-metadata.xml) : sa date de "
                     "publication, comparée aux commits des tâches, dit quel code a été testé.")
            env_values["MAVEN_REPOSITORY_USER"] = st.text_input(
                "MAVEN_REPOSITORY_USER", value=os.getenv("MAVEN_REPOSITORY_USER", ""))
            env_values["MAVEN_REPOSITORY_PASSWORD"] = secret_input(
                "MAVEN_REPOSITORY_PASSWORD")
        env_values["JENKINS_CUCUMBER_JOB"] = st.text_input("JENKINS_CUCUMBER_JOB", value=os.getenv("JENKINS_CUCUMBER_JOB", ""),
                                                           help="Job pré-rempli après génération d'un patch. "
                                                                "Ex: ODATIO_CUCUMBER_NIGHTLY")

        st.markdown("---")
        st.markdown("#### 📁 Dépôts, Conventions & Prompts")
        env_values["BITBUCKET_STD_REPO_PATH"] = st.text_input(
            "BITBUCKET_STD_REPO_PATH (Lien ou chemin du Socle Standard)",
            value=os.getenv("BITBUCKET_STD_REPO_PATH", ""),
            help="URL du projet Bitbucket/KISCloud (ex: https://git.kiscloud.io/projects/COMPONENT) "
                 "ou chemin local d'un clone (ex: C:\\development\\branch_dev)."
        )
        env_values["STD_FETCH_MAX_AGE_MINUTES"] = str(int(st.number_input(
            "STD_FETCH_MAX_AGE_MINUTES (réutilisation des copies du Standard)", min_value=0, step=60,
            value=env_int("STD_FETCH_MAX_AGE_MINUTES", 1440),
            help="Les copies locales du Standard (lues par Claude pendant les analyses) ne sont récupérées de "
                 "nouveau que si leur dernier fetch date de plus de ce délai, en minutes. 1440 = un jour ; "
                 "0 = à chaque analyse.")))
        env_values["ODATIO_VERSION"] = st.text_input(
            "ODATIO_VERSION (facultatif)", value=os.getenv("ODATIO_VERSION", ""), placeholder="ex. 26.16",
            help="Repli seulement : chaque client Cucumber a sa propre version du Standard, détectée dans le code de "
                 "son application, ses builds Jenkins et sa bibliothèque Cucumber (profil client, « Données du "
                 "projet »). Vide : lue dans le pom du Socle Standard (propriété odatio / kis, parent Odatio) ; une "
                 "version de dépôt comme 1.0.0 est ignorée.").strip()
        env_values["BITBUCKET_CLIENT_REPO_PATH"] = st.text_input(
            "BITBUCKET_CLIENT_REPO_PATH (Dépôt client par défaut)",
            value=os.getenv("BITBUCKET_CLIENT_REPO_PATH", ""),
            help="Pré-remplit le dépôt client de l'onglet Lancement. "
                 "Ex: https://git.kiscloud.io/projects/CLIENT ou C:\\development\\CLIENT"
        )
        env_values["BITBUCKET_CUCUMBER_STD_REPO"] = st.text_input(
            "BITBUCKET_CUCUMBER_STD_REPO",
            value=os.getenv("BITBUCKET_CUCUMBER_STD_REPO", ""),
            help="URL du dépôt Bitbucket contenant les scénarios Cucumber du socle Standard."
        )
        env_values["DEV_CONVENTIONS_PATH"] = st.text_input(
            "DEV_CONVENTIONS_PATH",
            value=os.getenv("DEV_CONVENTIONS_PATH", ""),
            help="Fichier Markdown des conventions de développement. "
                 "Vide = odatio-specific-code-conventions-dev.md du projet."
        )
        env_values["ODATIO_DOCS_URL"] = st.text_input(
            "ODATIO_DOCS_URL",
            value=os.getenv("ODATIO_DOCS_URL", ""),
            help="Page d'accueil de la documentation développeur ODATiO (https), consultée en lecture seule par "
                 "l'agent (cadrage, chiffrage, patch, revue de PR, tests Cucumber), limitée à ce site. "
                 "Vide = documentation non consultée."
        )
        env_values["PROMPTS_DIR"] = st.text_input(
            "PROMPTS_DIR",
            value=os.getenv("PROMPTS_DIR", ""),
            help="Dossier de prompts personnalisés : un fichier <nom>.md présent ici remplace le modèle du même nom "
                 "du dossier prompts/ du projet. Vide = modèles du projet."
        )

        st.markdown("---")
        st.markdown("#### 🥒 Tests Cucumber d'un client (onglet Cucumber)")
        with st.container(horizontal=True):
            cucumber_batch = st.number_input(
                "CUCUMBER_BATCH_SIZE", min_value=1, step=1, value=env_int("CUCUMBER_BATCH_SIZE", 3),
                help="Nombre de spécifiques par lot : un appel Claude neuf par lot.")
            cucumber_turns = st.number_input(
                "CUCUMBER_MAX_TURNS", min_value=5, step=5, value=env_int("CUCUMBER_MAX_TURNS", 60),
                help="Nombre maximal de tours de l'agent par lot (--max-turns). À 75 %, l'agent est invité à "
                     "finaliser ce qu'il a écrit et à rendre son JSON final.")
        env_values["CUCUMBER_BATCH_SIZE"] = str(int(cucumber_batch))
        env_values["CUCUMBER_MAX_TURNS"] = str(int(cucumber_turns))
        with st.container(horizontal=True):
            env_values["CUCUMBER_MAX_BUDGET_USD"] = st.text_input(
                "CUCUMBER_MAX_BUDGET_USD", value=os.getenv("CUCUMBER_MAX_BUDGET_USD", ""), placeholder="Vide = aucun",
                help="Budget maximal par appel Claude d'un lot, d'une correction ou d'une revue (--max-budget-usd). "
                     "Dépassement : lot en échec « budget atteint », fichiers annulés, repris par « Reprendre ».")
            inline_max = st.number_input(
                "CUCUMBER_INLINE_MAX_CHARS", min_value=0, step=2000,
                value=env_int("CUCUMBER_INLINE_MAX_CHARS", reglages.DEFAULT_INLINE_MAX_CHARS),
                help="Taille maximale (caractères) du contenu injecté dans le prompt d'un lot au lieu d'être lu par "
                     "l'agent : partie fixe (sommaire du catalogue, Step Definitions exemplaire, exemples des "
                     "recettes, runner à créer) et, séparément, extraits Standard et Jira du lot. Au-delà, le "
                     "chemin est donné pour lecture. 0 = rien d'injecté.")
        env_values["CUCUMBER_INLINE_MAX_CHARS"] = str(int(inline_max))
        env_values["CUCUMBER_INLINE_STUDIO_LINES"] = str(int(st.number_input(
            "CUCUMBER_INLINE_STUDIO_LINES", min_value=0, step=10,
            value=env_int("CUCUMBER_INLINE_STUDIO_LINES", reglages.DEFAULT_INLINE_STUDIO_LINES),
            help="Lignes au plus, par customized.xml, des parties modifiées (nœuds modifiés et leur contexte) "
                 "injectées dans le prompt du lot. Le reste du fichier est lu à la demande. 0 = rien d'injecté.")))
        env_values["CUCUMBER_INLINE_STEP_CODE_CHARS"] = str(int(st.number_input(
            "CUCUMBER_INLINE_STEP_CODE_CHARS", min_value=0, step=2000,
            value=env_int("CUCUMBER_INLINE_STEP_CODE_CHARS", reglages.DEFAULT_INLINE_STEP_CODE_CHARS),
            help="Taille maximale (caractères) du code des steps Standard candidats injecté dans le prompt d'un lot "
                 "(signature et corps : colonnes des tables, clés de contexte, helpers appelés), 8 steps au plus "
                 "par spécifique. 0 = rien d'injecté (l'agent lit le code dans la copie des sources du Standard).")))
        lean = st.checkbox(
            "CLAUDE_LEAN_SESSION", value=os.getenv("CLAUDE_LEAN_SESSION", "1").strip() != "0",
            help="Sessions Cucumber allégées : seules les définitions des outils autorisés sont envoyées (--tools), "
                 "sans serveur MCP (--strict-mcp-config), commandes ni skills (--disable-slash-commands), sections "
                 "propres au poste hors du prompt système. Décoché = comportement antérieur.")
        env_values["CLAUDE_LEAN_SESSION"] = "1" if lean else "0"
        env_values["CUCUMBER_EXCLUDE_GLOBS"] = st.text_input(
            "CUCUMBER_EXCLUDE_GLOBS", value=os.getenv("CUCUMBER_EXCLUDE_GLOBS", ""),
            placeholder="Vide = exclusions par défaut (pom.xml, tests unitaires, ressources statiques…)",
            help="Motifs (séparés par des virgules) des fichiers techniques exclus de l'inventaire des spécifiques. "
                 "Une valeur remplace entièrement la liste par défaut : tools/cucumber_tools.py, DEFAULT_EXCLUDE_GLOBS.")
        env_values["CUCUMBER_EXCLUDE_REPOS"] = st.text_input(
            "CUCUMBER_EXCLUDE_REPOS", value=os.getenv("CUCUMBER_EXCLUDE_REPOS", ""),
            placeholder="Vide = *-frontend, *-front, *-ui",
            help="Motifs des dépôts clients exclus de l'inventaire : les dépôts front (écrans) ne sont pas testables "
                 "avec les steps du Cucumber Standard.")
        env_values["CUCUMBER_PARAM_GLOBS"] = st.text_input(
            "CUCUMBER_PARAM_GLOBS", value=os.getenv("CUCUMBER_PARAM_GLOBS", ""),
            placeholder="Vide = *.properties, *i18n*, *messages*, *.sql, *param*.xml, *parameter*, *.csv",
            help="Motifs des fichiers classés « parametrage » (paramètres et messages).")
        try:
            match_min = float(os.getenv("CUCUMBER_STD_MATCH_MIN", "").strip() or reglages.DEFAULT_STD_MATCH_MIN)
        except ValueError:
            match_min = reglages.DEFAULT_STD_MATCH_MIN
        env_values["CUCUMBER_STD_MATCH_MIN"] = str(st.number_input(
            "CUCUMBER_STD_MATCH_MIN", min_value=0.0, max_value=1.0, step=0.05, value=match_min,
            help="Score minimal d'une feature du Cucumber Standard pour être rapprochée d'un spécifique (au plus 3, "
                 "dont l'agent part pour écrire ses tests). Plus haut = moins de rapprochements, plus sûrs. Pris en "
                 "compte au prochain inventaire."))
        try:
            dup_threshold = float(os.getenv("CUCUMBER_DUP_THRESHOLD", "").strip()
                                  or reglages.DEFAULT_DUP_THRESHOLD)
        except ValueError:
            dup_threshold = reglages.DEFAULT_DUP_THRESHOLD
        with st.container(horizontal=True):
            dup_value = st.number_input(
                "CUCUMBER_DUP_THRESHOLD", min_value=0.5, max_value=1.0, step=0.05, value=dup_threshold,
                help="Similarité (titre ou steps) au-delà de laquelle un scénario généré est signalé comme doublon "
                     "d'un scénario Standard ou client, puis envoyé à la revue.")
            review_turns = st.number_input(
                "CUCUMBER_REVIEW_MAX_TURNS", min_value=3, step=1, value=env_int("CUCUMBER_REVIEW_MAX_TURNS", 8),
                help="Nombre maximal de tours de l'appel de revue des scénarios signalés d'un lot (--max-turns).")
        env_values["CUCUMBER_DUP_THRESHOLD"] = str(dup_value)
        env_values["CUCUMBER_REVIEW_MAX_TURNS"] = str(int(review_turns))
        env_values["CUCUMBER_COMPILE_CMD"] = st.text_input(
            "CUCUMBER_COMPILE_CMD", value=os.getenv("CUCUMBER_COMPILE_CMD", reglages.DEFAULT_COMPILE_CMD),
            placeholder="Vide = pas de compilation",
            help="Commande de compilation lancée après chaque lot (en Python, sans token). Dépôt client sans pom.xml : "
                 "compilé par-dessus une copie du Cucumber Standard. Hors ligne (-o) en échec faute de dépendances : "
                 "relancée une fois en ligne.")
        with st.container(horizontal=True):
            env_values["CUCUMBER_JAVA21_HOME"] = st.text_input(
                "CUCUMBER_JAVA21_HOME", value=os.getenv("CUCUMBER_JAVA21_HOME", ""),
                help="Dossier d'un JDK 21 pour la compilation et le dry-run locaux de la migration Java 21 (vide : "
                     "JDK courant de Maven, qui doit être en 21).")
            java21_days = st.number_input(
                "CUCUMBER_JAVA21_REFERENCE_DAYS", min_value=0, step=1,
                value=env_int("CUCUMBER_JAVA21_REFERENCE_DAYS", reglages.DEFAULT_REFERENCE_DAYS),
                help="Âge maximal (jours) d'un run complet vert réutilisé comme run AVANT de la migration Java 21 "
                     "(0 : toujours relancé).")
        env_values["CUCUMBER_JAVA21_REFERENCE_DAYS"] = str(int(java21_days))
        with st.container(horizontal=True):
            fix_attempts = st.number_input(
                "CUCUMBER_FIX_ATTEMPTS", min_value=0, step=1, value=env_int("CUCUMBER_FIX_ATTEMPTS", 2),
                help="Appels de correction au plus par lot si la compilation échoue (0 = aucun).")
            fix_turns = st.number_input(
                "CUCUMBER_FIX_MAX_TURNS", min_value=3, step=1, value=env_int("CUCUMBER_FIX_MAX_TURNS", 10),
                help="Nombre maximal de tours de l'agent par appel de correction (--max-turns).")
        with st.container(horizontal=True):
            fix_max = st.number_input(
                "CUCUMBER_FIX_MAX_ATTEMPTS", min_value=0, step=1, value=env_int("CUCUMBER_FIX_MAX_ATTEMPTS", 6),
                help="Corrections au plus par lot, au total : au-delà des CUCUMBER_FIX_ATTEMPTS premières, avec le "
                     "modèle du lot, tant que les erreurs évoluent (arrêt si les mêmes erreurs reviennent).")
            fix_lot_turns = st.number_input(
                "CUCUMBER_FIX_LOT_MAX_TURNS", min_value=3, step=1, value=env_int("CUCUMBER_FIX_LOT_MAX_TURNS", 25),
                help="Tours de l'agent par correction faite avec le modèle du lot.")
        env_values["CUCUMBER_FIX_ATTEMPTS"] = str(int(fix_attempts))
        env_values["CUCUMBER_FIX_MAX_TURNS"] = str(int(fix_turns))
        with st.container(horizontal=True):
            env_values["CLAUDE_MODEL_CUCUMBER_VERIFY_FIX"] = st.text_input(
                "CLAUDE_MODEL_CUCUMBER_VERIFY_FIX", value=os.getenv("CLAUDE_MODEL_CUCUMBER_VERIFY_FIX", ""),
                help="Modèle des corrections après exécution Jenkins. Vide : modèle du lot, en reprenant sa session "
                     "(tout le contexte du lot est relu à chaque correction : le plus cher). Renseigné (ex. sonnet) : "
                     "appel neuf avec ce modèle, sur le seul prompt de correction.")
            env_values["CLAUDE_EFFORT_CUCUMBER_VERIFY_FIX"] = st.text_input(
                "CLAUDE_EFFORT_CUCUMBER_VERIFY_FIX", value=os.getenv("CLAUDE_EFFORT_CUCUMBER_VERIFY_FIX", ""),
                help="Effort de ces corrections (low, medium, high…). Vide : CLAUDE_EFFORT_CUCUMBER_FIX.")
        env_values["CUCUMBER_FIX_MAX_ATTEMPTS"] = str(int(fix_max))
        env_values["CUCUMBER_FIX_LOT_MAX_TURNS"] = str(int(fix_lot_turns))
        dry_run = st.checkbox(
            "CUCUMBER_DRY_RUN", value=os.getenv("CUCUMBER_DRY_RUN", "1").strip() != "0",
            help="Après une compilation réussie, dry-run Cucumber des features du lot : vérifie que chaque step a "
                 "une définition (mvn test -Dcucumber.execution.dry-run=true), sans rien exécuter.")
        env_values["CUCUMBER_DRY_RUN"] = "1" if dry_run else "0"
        agent_compile = st.checkbox(
            "CUCUMBER_AGENT_COMPILE", value=os.getenv("CUCUMBER_AGENT_COMPILE", "1").strip() != "0",
            help="L'agent d'un lot (et de la revue / des corrections) compile lui-même le worktree pendant qu'il "
                 "écrit (CUCUMBER_COMPILE_CMD, test-compile : rien n'est exécuté) et corrige ses erreurs avec tout le "
                 "contexte du lot. Décoché = compilation après le lot seulement, corrigée par CLAUDE_MODEL_CUCUMBER_FIX.")
        env_values["CUCUMBER_AGENT_COMPILE"] = "1" if agent_compile else "0"
        agent_jenkins = st.checkbox(
            "CUCUMBER_AGENT_JENKINS", value=reglages.agent_runs_tests(),
            help="Coché par défaut dès que JENKINS_USER et JENKINS_API_TOKEN sont renseignés (boucle fermée). "
                 "Décoché : l'agent livre un patch non vérifié, ne pousse rien et ne lance aucun test ; vous appliquez le "
                 "patch, lancez votre job complet et importez son résultat. Coché : l'agent vérifie en autonomie "
                 "pendant la génération — il pousse ses tests sur sa branche de travail (profil client), lance le "
                 "job du client (profil ou CUCUMBER_VERIFY_JOB) avec les paramètres imposés du profil, un build à la "
                 "fois, corrige et revérifie, puis livre un patch vérifié à appliquer sur votre branche.")
        env_values["CUCUMBER_AGENT_JENKINS"] = "1" if agent_jenkins else "0"
        st.markdown("##### Vérification de la pertinence par exécution")
        st.caption("Sans job ni commande locale, la génération s'arrête au contrôle statique et affiche « pertinence "
                   "non vérifiée par exécution ».")
        env_values["CUCUMBER_VERIFY_JOB"] = st.text_input(
            "CUCUMBER_VERIFY_JOB", value=os.getenv("CUCUMBER_VERIFY_JOB", ""),
            placeholder="Vide = pas de job (ex. CUCUMBER-VERIFY-PATCH)",
            help="Job Jenkins de vérification. Avec un paramètre fichier PATCH (.patch du lot), TAGS et ENV : aucun "
                 "push. Sans PATCH : le job lit la branche de travail du profil client, où l'agent pousse ses tests "
                 "(jamais la branche d'intégration) ; les rôles de ses paramètres (branche, dépôt, arguments, "
                 "attente) sont détectés et corrigeables dans le profil client.")
        with st.container(horizontal=True):
            env_values["CUCUMBER_VERIFY_ENV"] = st.text_input(
                "CUCUMBER_VERIFY_ENV", value=os.getenv("CUCUMBER_VERIFY_ENV", ""),
                help="Environnement client (paramètre ENV du job, {env} de la commande locale).")
            env_values["CUCUMBER_VERIFY_ENV_STD"] = st.text_input(
                "CUCUMBER_VERIFY_ENV_STD", value=os.getenv("CUCUMBER_VERIFY_ENV_STD", ""),
                help="Environnement Standard de même version, pour la contre-épreuve des overrides (vide = pas de "
                     "contre-épreuve Standard).")
        env_values["CUCUMBER_INIT_TAGS"] = st.text_input(
            "CUCUMBER_INIT_TAGS", value=os.getenv("CUCUMBER_INIT_TAGS", ""), placeholder="Ex. @INIT_CLIENT_WMS",
            help="Initialisation du client, exécutée une fois par génération avant de juger les lots. En échec : "
                 "la vérification s'arrête, les scénarios sont « non vérifiés — environnement non initialisé ». "
                 "Désigne aussi les features d'init dont les codes créés sont imposés aux lots (défaut : tag "
                 "@INIT_*, dossier 0x-Contexte). Vide = pas d'initialisation préalable.")
        env_values["CUCUMBER_DETECTION_INTERVAL"] = st.text_input(
            "CUCUMBER_DETECTION_INTERVAL", value=os.getenv("CUCUMBER_DETECTION_INTERVAL", ""), placeholder="900",
            help="Onglet Cucumber : délai en secondes entre deux contrôles des commits des dépôts du projet ; la "
                 "détection automatique (inventaire, profil, état de l'init) n'est refaite que s'ils ont changé.")
        env_values["CUCUMBER_LOCAL_SCHEMA"] = st.text_input(
            "CUCUMBER_LOCAL_SCHEMA", value=os.getenv("CUCUMBER_LOCAL_SCHEMA", ""),
            placeholder="cucumber_local_{client}",
            help="Schéma des essais locaux, cible de la copie de la config client (étape 3) ; {client} = nom du client. "
                 "Jamais un schéma du pool des runs Jenkins. L'agent ne le lit que s'il est choisi comme schéma du "
                 "projet.")
        env_values["CUCUMBER_POOL_SCHEMA_PATTERN"] = st.text_input(
            "CUCUMBER_POOL_SCHEMA_PATTERN", value=os.getenv("CUCUMBER_POOL_SCHEMA_PATTERN", ""),
            placeholder=r"cucumber_customer_\d+",
            help="Expression régulière des schémas des runs Jenkins (un par slot) : aucune copie de base n'y écrit.")
        env_values["CUCUMBER_REFERENCE_SCHEMA"] = st.text_input(
            "CUCUMBER_REFERENCE_SCHEMA", value=os.getenv("CUCUMBER_REFERENCE_SCHEMA", ""),
            help="Diagnostic de l'environnement (clear en échec reproductible) : schéma de référence de la version, "
                 "dont les contraintes de clé étrangère servent de comparaison. Vide : un autre slot du pool.")
        env_values["CLAUDE_MODEL_ENV_DIAGNOSIS"] = st.text_input(
            "CLAUDE_MODEL_ENV_DIAGNOSIS", value=os.getenv("CLAUDE_MODEL_ENV_DIAGNOSIS", ""), placeholder="haiku",
            help="Modèle qui résume les logs serveur de la purge via Groundcover (lecture seule) pendant le "
                 "diagnostic de l'environnement.")
        env_values["CUCUMBER_RUN_JOB"] = st.text_input(
            "CUCUMBER_RUN_JOB", value=os.getenv("CUCUMBER_RUN_JOB", ""), placeholder="DOSSIER/JOB",
            help="Run Jenkins automatique (onglet Cucumber, mode expert, « Résultats ») : job qui exécute <client>-cucumber, si "
                 "le profil client n'en donne pas. Il doit lire la branche du profil et accepter TAGS.")
        env_values["CUCUMBER_VERIFY_LOCAL_CMD"] = st.text_input(
            "CUCUMBER_VERIFY_LOCAL_CMD", value=os.getenv("CUCUMBER_VERIFY_LOCAL_CMD", ""),
            placeholder='mvn -q test "-Dcucumber.filter.tags={tags}" -Denv={env}',
            help="Repli local (si pas de job) : commande lancée dans le dossier de compilation ; {tags} et {env} "
                 "sont remplacés ; le rapport target/**/cucumber*.json est lu.")
        with st.container(horizontal=True):
            verify_timeout = st.number_input(
                "CUCUMBER_VERIFY_TIMEOUT", min_value=60, step=60, value=env_int("CUCUMBER_VERIFY_TIMEOUT", 3600),
                help="Durée maximale d'une exécution (build Jenkins ou commande locale), en secondes.")
            verify_fixes = st.number_input(
                "CUCUMBER_VERIFY_FIX_MAX", min_value=0, step=1, value=env_int("CUCUMBER_VERIFY_FIX_MAX", 4),
                help="Corrections au plus par lot après exécution. L'agent continue tant que l'échec change et "
                     "s'arrête pour un scénario dès que son échec revient à l'identique. 0 : aucune correction.")
            review_sample = st.number_input(
                "CUCUMBER_REVIEW_SAMPLE", min_value=0, step=1, value=env_int("CUCUMBER_REVIEW_SAMPLE", 0),
                help="Scénarios proposés à la revue humaine de l'onglet Cucumber : d'abord les « non vérifié », "
                     "puis des « pertinent » tirés au hasard.")
        env_values["CUCUMBER_VERIFY_TIMEOUT"] = str(int(verify_timeout))
        env_values["CUCUMBER_VERIFY_FIX_MAX"] = str(int(verify_fixes))
        env_values["CUCUMBER_VERSION_BRANCH_WRITE"] = "1" if st.checkbox(
            "Écrire directement sur les branches de version du dépôt <client>-cucumber (v1.1, v1.2…)",
            value=os.getenv("CUCUMBER_VERSION_BRANCH_WRITE", "1").strip() != "0",
            help="Étape 4 de l'onglet Cucumber : diff validé puis poussé sur la branche de la version (des tests "
                 "seulement), jamais sur la branche d'intégration ni en forçant. Décoché : branche de l'agent et patch "
                 "à appliquer (CUCUMBER_VERSION_BRANCH_WRITE).") else "0"
        # Ancien réglage (plafond fixe) : remplacé par le plafond adaptatif, 0 conservé comme « aucune correction »
        env_values["CUCUMBER_VERIFY_FIX_ATTEMPTS"] = "0" if int(verify_fixes) == 0 else ""
        env_values["CUCUMBER_REVIEW_SAMPLE"] = str(int(review_sample))
        with st.container(horizontal=True):
            slow_seconds = st.number_input(
                "CUCUMBER_SLOW_SCENARIO_SECONDS", min_value=10, step=30,
                value=env_int("CUCUMBER_SLOW_SCENARIO_SECONDS", 300),
                help="Import d'un run : un scénario est anormalement long au-delà de cette durée (ou de 3 fois la "
                     "durée médiane du run).")
            export_cap = st.number_input(
                "CUCUMBER_LOG_EXPORT_MAX_LINES", min_value=0, step=1000,
                value=env_int("CUCUMBER_LOG_EXPORT_MAX_LINES", 0),
                help="Plafond de lignes de l'outil d'export des logs serveur (0 : inconnu) : rappelé dans les "
                     "fenêtres à exporter ; un export qui l'atteint est signalé.")
            max_cause = st.number_input(
                "CUCUMBER_MAX_CAUSE_ATTEMPTS", min_value=1, step=1, value=env_int("CUCUMBER_MAX_CAUSE_ATTEMPTS", 3),
                help="Corrections infructueuses sur une même cause (même message, valeurs variables masquées) avant "
                     "que l'agent arrête de deviner et propose une méthode de preuve.")
        env_values["CUCUMBER_MAX_CAUSE_ATTEMPTS"] = str(int(max_cause))
        env_values["CUCUMBER_SERVER_LOG_DIR"] = st.text_input(
            "CUCUMBER_SERVER_LOG_DIR", value=os.getenv("CUCUMBER_SERVER_LOG_DIR", ""),
            help="Répertoire où sont déposés les logs applicatifs du serveur de l'environnement de test (*.log, "
                 "rotations, .gz) : à chaque import d'un run, la fenêtre de chaque échec y est extraite (secrets "
                 "masqués) et donnée au correcteur, sans export ni réimport manuel. Vide : non utilisé.").strip()
        st.markdown("**Logs de l'environnement de test dans groundcover** (lecture seule)")
        with st.container(horizontal=True):
            env_values["GROUNDCOVER_API_KEY"] = secret_input(
                "GROUNDCOVER_API_KEY",
                help="Clé d'API groundcover (Settings → Access → API Keys), de préférence d'un compte en lecture "
                     "seule.").strip()
            env_values["GROUNDCOVER_BACKEND_ID"] = st.text_input(
                "GROUNDCOVER_BACKEND_ID", value=os.getenv("GROUNDCOVER_BACKEND_ID", ""),
                help="Identifiant du backend (comptes multi-backend seulement, même écran que la clé).").strip()
            env_values["GROUNDCOVER_API_URL"] = st.text_input(
                "GROUNDCOVER_API_URL", value=os.getenv("GROUNDCOVER_API_URL", ""),
                placeholder="https://api.groundcover.com").strip()
        env_values["CUCUMBER_GROUNDCOVER_FILTER"] = st.text_input(
            "CUCUMBER_GROUNDCOVER_FILTER", value=os.getenv("CUCUMBER_GROUNDCOVER_FILTER", ""),
            placeholder="namespace:apparel-cucumber",
            help="Filtre gcQL de l'environnement où Jenkins joue les tests (namespace, workload, cluster…), ajouté à "
                 "toute requête : obligatoire, sans lui les logs groundcover ne sont pas lus.").strip()
        env_values["CUCUMBER_SLOW_SCENARIO_SECONDS"] = str(int(slow_seconds))
        env_values["CUCUMBER_LOG_EXPORT_MAX_LINES"] = str(int(export_cap)) if export_cap else ""
        jacoco = st.checkbox(
            "CUCUMBER_JACOCO", value=os.getenv("CUCUMBER_JACOCO", "").strip() == "1",
            help="Environnement client lancé avec l'agent JaCoCo : part des classes de chaque spécifique exécutées "
                 "(jacoco.exec archivé par le job, ou CUCUMBER_JACOCO_EXEC en local).")
        env_values["CUCUMBER_JACOCO"] = "1" if jacoco else "0"
        keep_tags = st.checkbox(
            "CUCUMBER_KEEP_AGENT_TAGS", value=os.getenv("CUCUMBER_KEEP_AGENT_TAGS", "").strip() == "1",
            help="Conserve les tags de vérification @agent_<client>_<lot>[_<n>] dans le patch livré (par défaut, "
                 "ils sont retirés du patch et gardés seulement dans le worktree).")
        env_values["CUCUMBER_KEEP_AGENT_TAGS"] = "1" if keep_tags else "0"
        env_values["CUCUMBER_JACOCO_EXEC"] = st.text_input(
            "CUCUMBER_JACOCO_EXEC", value=os.getenv("CUCUMBER_JACOCO_EXEC", ""),
            help="Mode local : chemin du jacoco.exec produit par l'environnement client.")

        st.markdown("---")
        st.markdown("#### :material/hub: Serveurs MCP")
        st.caption("Par défaut, les appels Claude de l'agent ne chargent aucun serveur MCP (--strict-mcp-config). "
                   "Seul Groundcover peut être ajouté, en lecture des logs serveur, pour les étapes de diagnostic "
                   "(échecs d'un run, correction de l'init, correction après exécution) ; jamais pour l'analyse d'un "
                   "ticket ni la génération d'un patch. Aucun jeton dans le .env : la connexion se fait par "
                   "navigateur et reste dans le dossier de configuration de Claude Code (compte Claude ci-dessous).")
        env_values[mcp_servers.ENABLED_ENV] = "1" if st.checkbox(
            "Groundcover activé", value=os.getenv(mcp_servers.ENABLED_ENV, "").strip() == "1",
            help="GROUNDCOVER_MCP_ENABLED : donne le serveur MCP Groundcover (outils de lecture seulement) aux "
                 "étapes de diagnostic.") else "0"
        env_values[mcp_servers.URL_ENV] = st.text_input(
            "Adresse du serveur MCP Groundcover", value=os.getenv(mcp_servers.URL_ENV, ""),
            placeholder="Adresse donnée par « Connect to MCP » dans Groundcover",
            help="GROUNDCOVER_MCP_URL. Collez l'adresse, ou la commande / la configuration JSON proposée par « Connect "
                 "to MCP » : l'adresse et les en-têtes non secrets en sont extraits à l'enregistrement. Un en-tête "
                 "d'authentification (jeton, clé) est refusé.").strip()
        with st.container(horizontal=True):
            env_values[mcp_servers.HEADERS_ENV] = st.text_input(
                "En-têtes non secrets (facultatif)", value=os.getenv(mcp_servers.HEADERS_ENV, ""),
                placeholder="Nom: valeur ; Nom: valeur",
                help="GROUNDCOVER_MCP_HEADERS : en-têtes demandés par « Connect to MCP » (fuseau horaire, "
                     "identifiant de tenant…). Jamais de jeton.").strip()
            env_values[mcp_servers.TOOLS_ENV] = st.text_input(
                "Outils autorisés (facultatif)", value=os.getenv(mcp_servers.TOOLS_ENV, ""),
                placeholder="Vide : outils de lecture relevés par « Tester »",
                help="GROUNDCOVER_MCP_TOOLS : noms complets (mcp__groundcover__…), séparés par des virgules. Seuls "
                     "les outils de lecture (get, list, query, search…) sont gardés.").strip()

        st.markdown("---")
        st.markdown("#### 🔑 Compte Claude")
        # Valeur du .env seule : celle héritée du shell n'est pas enregistrée par défaut
        saved_config_dir = (dotenv_values(ENV_PATH).get("CLAUDE_CONFIG_DIR") or "").strip()
        config_dir_options = [""] + claude_config_dirs()
        if saved_config_dir and saved_config_dir not in config_dir_options:
            config_dir_options.append(saved_config_dir)
        shell_config_dir = os.environ.get("AGENT_SHELL_CLAUDE_CONFIG_DIR", "")

        def config_dir_label(config_dir: str) -> str:
            label = config_dir or ("Défaut du poste" + (f" ({shell_config_dir})" if shell_config_dir else " (~/.claude)"))
            account = claude_account(config_dir or shell_config_dir)
            return f"{label} — {account}" if account else label

        env_values["CLAUDE_CONFIG_DIR"] = st.selectbox(
            "CLAUDE_CONFIG_DIR", config_dir_options, index=config_dir_options.index(saved_config_dir),
            format_func=config_dir_label, accept_new_options=True,
            help="Dossier de configuration de Claude Code du compte à utiliser (abonnement Pro / Max / Team). "
                 "Proposés : ~/.claude et ~/.claude-* du poste ; saisissez un autre chemin si besoin. "
                 "Pour connecter un nouveau compte : CLAUDE_CONFIG_DIR=<dossier> puis `claude auth login`.")
        env_values["ANTHROPIC_API_KEY"] = secret_input(
            "ANTHROPIC_API_KEY", label="ANTHROPIC_API_KEY (Laissez vide pour utiliser le compte Claude ci-dessus)",
            help="Renseignée, la clé API est utilisée à la place du compte Claude.")

        st.markdown("---")
        st.markdown("#### 💸 Optimisation des tokens")
        jira_context_max = st.number_input(
            "JIRA_CONTEXT_MAX_CHARS", min_value=0, max_value=200000, step=1000,
            value=env_int("JIRA_CONTEXT_MAX_CHARS", 12000),
            help="Taille maximale du contexte Jira (description, Épopée, COST) injecté dans le prompt de cadrage. "
                 "0 = pas d'injection : l'agent interroge Jira lui-même."
        )
        memory_max = st.number_input(
            "MEMORY_MAX_CHARS", min_value=0, max_value=100000, step=1000,
            value=env_int("MEMORY_MAX_CHARS", 6000),
            help="Taille maximale des retours d'expérience injectés dans les prompts. 0 = désactivé."
        )
        env_values["JIRA_CONTEXT_MAX_CHARS"] = str(int(jira_context_max))
        env_values["MEMORY_MAX_CHARS"] = str(int(memory_max))
        model_help = ("Alias (sonnet, opus, haiku) ou identifiant complet du modèle, transmis avec --model. "
                      "Vide = modèle par défaut de Claude Code.")
        with st.container(horizontal=True):
            env_values["CLAUDE_MODEL_CADRAGE"] = st.text_input("CLAUDE_MODEL_CADRAGE", value=os.getenv("CLAUDE_MODEL_CADRAGE", ""), help=model_help)
            env_values["CLAUDE_MODEL_REFINE"] = st.text_input("CLAUDE_MODEL_REFINE", value=os.getenv("CLAUDE_MODEL_REFINE", ""), help=model_help)
        with st.container(horizontal=True):
            env_values["CLAUDE_MODEL_PATCH"] = st.text_input("CLAUDE_MODEL_PATCH", value=os.getenv("CLAUDE_MODEL_PATCH", ""), help=model_help)
        with st.container(horizontal=True):
            env_values["CLAUDE_MODEL_BESOIN"] = st.text_input("CLAUDE_MODEL_BESOIN", value=os.getenv("CLAUDE_MODEL_BESOIN", ""),
                                                              help=model_help + " Utilisé par la rédaction du besoin (onglet Intégration).")
            env_values["CLAUDE_MODEL_CUCUMBER"] = st.text_input("CLAUDE_MODEL_CUCUMBER", value=os.getenv("CLAUDE_MODEL_CUCUMBER", ""),
                                                                help=model_help.replace("Vide = modèle par défaut de Claude Code.", "Vide = sonnet.")
                                                                + " Utilisé par chaque lot de la génération des tests Cucumber d'un client (onglet Cucumber).")
        env_values["CLAUDE_MODEL_CUCUMBER_FIX"] = st.text_input("CLAUDE_MODEL_CUCUMBER_FIX", value=os.getenv("CLAUDE_MODEL_CUCUMBER_FIX", ""),
                                                                placeholder="Vide = haiku",
                                                                help=model_help.replace("Vide = modèle par défaut de Claude Code.", "Vide = haiku.")
                                                                + " Utilisé par les corrections de compilation et de dry-run des lots Cucumber ; si elles échouent, "
                                                                  "une dernière tentative est faite avec CLAUDE_MODEL_CUCUMBER.")
        effort_help = ("Niveau d'effort transmis avec --effort : low, medium, high, xhigh ou max. "
                       "Vide = réglage de Claude Code sur le poste.")
        with st.container(horizontal=True):
            env_values["CLAUDE_EFFORT_CADRAGE"] = st.text_input("CLAUDE_EFFORT_CADRAGE", value=os.getenv("CLAUDE_EFFORT_CADRAGE", ""), help=effort_help)
            env_values["CLAUDE_EFFORT_REFINE"] = st.text_input("CLAUDE_EFFORT_REFINE", value=os.getenv("CLAUDE_EFFORT_REFINE", ""), help=effort_help)
        with st.container(horizontal=True):
            env_values["CLAUDE_EFFORT_PATCH"] = st.text_input("CLAUDE_EFFORT_PATCH", value=os.getenv("CLAUDE_EFFORT_PATCH", ""), help=effort_help)
        with st.container(horizontal=True):
            env_values["CLAUDE_EFFORT_BESOIN"] = st.text_input("CLAUDE_EFFORT_BESOIN", value=os.getenv("CLAUDE_EFFORT_BESOIN", ""),
                                                               help=effort_help + " Utilisé par la rédaction du besoin (onglet Intégration).")
            env_values["CLAUDE_EFFORT_CUCUMBER"] = st.text_input("CLAUDE_EFFORT_CUCUMBER", value=os.getenv("CLAUDE_EFFORT_CUCUMBER", "medium"),
                                                                 help=effort_help + " Utilisé par chaque lot de la génération des tests Cucumber d'un client.")
        env_values["CLAUDE_EFFORT_CUCUMBER_FIX"] = st.text_input("CLAUDE_EFFORT_CUCUMBER_FIX", value=os.getenv("CLAUDE_EFFORT_CUCUMBER_FIX", "low"),
                                                                 help=effort_help.replace("Vide = réglage de Claude Code sur le poste.", "Vide = low.")
                                                                 + " Utilisé par les appels de correction et de revue des lots Cucumber.")
        st.caption("La consommation de chaque exécution est affichée (ligne [TOKENS]) et ajoutée à .tmp/token_usage.csv, "
                   "avec le modèle et l'effort utilisés.")

        st.markdown("---")
        st.markdown("#### 🗄️ Connexion Base de données (lecture seule)")
        current_db_type = os.getenv("DB_TYPE", "postgresql")
        env_values["DB_TYPE"] = st.selectbox("DB_TYPE", DB_TYPES,
                                             index=DB_TYPES.index(current_db_type) if current_db_type in DB_TYPES else 0)
        with st.container(horizontal=True):
            env_values["DB_HOST"] = st.text_input("DB_HOST", value=os.getenv("DB_HOST", ""))
            env_values["DB_PORT"] = st.text_input("DB_PORT", value=os.getenv("DB_PORT", ""), help="Vide = port par défaut du SGBD.")
        env_values["DB_NAME"] = st.text_input("DB_NAME (base, ou service name Oracle)", value=os.getenv("DB_NAME", ""))
        with st.container(horizontal=True):
            env_values["DB_USER"] = st.text_input("DB_USER", value=os.getenv("DB_USER", ""))
            env_values["DB_PASSWORD"] = secret_input("DB_PASSWORD")
        env_values["DB_SCHEMA"] = st.text_input(
            "DB_SCHEMA (valeur par défaut)", value=os.getenv("DB_SCHEMA", ""),
            help="Pré-remplit le champ « Schéma BDD cible » de l'onglet Lancement."
        )
        env_values["DB_ODBC_DRIVER"] = st.text_input(
            "DB_ODBC_DRIVER", value=os.getenv("DB_ODBC_DRIVER", "ODBC Driver 18 for SQL Server"),
            help="SQL Server via pyodbc uniquement : nom du pilote ODBC installé sur le poste."
        )
        st.caption("Utilisez de préférence un compte base en lecture seule : le mot de passe reste dans le .env "
                   "et n'est jamais transmis à l'agent.")

        st.markdown("#### 🥒 Base de l'environnement de test Cucumber (lecture seule)")
        st.caption("Base de l'environnement où Jenkins joue les tests (CUCUMBER_VERIFY_ENV) : l'agent des lots Cucumber "
                   "y prend ses données de référence réelles. Vide = base ci-dessus (DB_*). Ne mettez pas ces "
                   "informations dans CUCUMBER_VERIFY_ENV, transmis tel quel à Jenkins.")
        current_cuc_type = os.getenv("CUCUMBER_DB_TYPE", "") or current_db_type
        env_values["CUCUMBER_DB_TYPE"] = st.selectbox(
            "CUCUMBER_DB_TYPE", DB_TYPES, index=DB_TYPES.index(current_cuc_type) if current_cuc_type in DB_TYPES else 0)
        with st.container(horizontal=True):
            env_values["CUCUMBER_DB_HOST"] = st.text_input("CUCUMBER_DB_HOST", value=os.getenv("CUCUMBER_DB_HOST", ""))
            env_values["CUCUMBER_DB_PORT"] = st.text_input("CUCUMBER_DB_PORT", value=os.getenv("CUCUMBER_DB_PORT", ""),
                                                           help="Vide = port par défaut du SGBD.")
        env_values["CUCUMBER_DB_NAME"] = st.text_input("CUCUMBER_DB_NAME (base, ou service name Oracle)",
                                                       value=os.getenv("CUCUMBER_DB_NAME", ""))
        with st.container(horizontal=True):
            env_values["CUCUMBER_DB_USER"] = st.text_input("CUCUMBER_DB_USER", value=os.getenv("CUCUMBER_DB_USER", ""))
            env_values["CUCUMBER_DB_PASSWORD"] = secret_input("CUCUMBER_DB_PASSWORD")
        env_values["CUCUMBER_DB_SCHEMA"] = st.text_input(
            "CUCUMBER_DB_SCHEMA (valeur par défaut)", value=os.getenv("CUCUMBER_DB_SCHEMA", ""),
            help="Pré-remplit le champ « Schéma BDD » de l'onglet Cucumber.")

        st.markdown("**Base client source de la config recopiée dans la base des tests** (vide : connexion DB_*)")
        with st.container(horizontal=True):
            for key, default, kind in (("CLIENT_SOURCE_DB_HOST", "", "default"), ("CLIENT_SOURCE_DB_PORT", "", "default"),
                                       ("CLIENT_SOURCE_DB_NAME", "", "default"), ("CLIENT_SOURCE_DB_USER", "", "default"),
                                       ("CLIENT_SOURCE_DB_PASSWORD", "", "password")):
                env_values[key] = secret_input(key) if kind == "password" else                     st.text_input(key, value=os.getenv(key, default))
        with st.container(horizontal=True):
            env_values["PG_BIN_DIR"] = st.text_input(
                "PG_BIN_DIR", value=os.getenv("PG_BIN_DIR", ""), placeholder=r"C:\Program Files\PostgreSQL\16\bin",
                help="Dossier de pg_dump, pg_restore et psql. Vide : ceux du PATH.").strip()
            env_values["RESTORE_POST_SQL"] = st.text_input(
                "RESTORE_POST_SQL", value=os.getenv("RESTORE_POST_SQL", ""),
                help="Script SQL passé après la recopie (comme Script_a_passer_apres_import.sql), précédé de "
                     "« set schema '<schéma des tests>'; ». Vide : aucun.").strip()
            env_values["RESTORE_EXCLUDE_DATA"] = st.text_input(
                "RESTORE_EXCLUDE_DATA", value=os.getenv("RESTORE_EXCLUDE_DATA", ""),
                help="Tables dont les DONNÉES ne sont pas recopiées, en plus de celles du script (historiques, "
                     "journaux), séparées par des virgules.").strip()

        st.markdown("---")
        st.markdown("#### :material/folder_shared: Connaissances partagées")
        with st.container(horizontal=True):
            env_values["SKILLS_AUTO"] = "1" if st.checkbox(
                "Auto-amélioration (compétences)", value=os.getenv("SKILLS_AUTO", "1").strip() != "0",
                help="Après une tâche complexe réussie et vérifiée, l'agent enregistre la procédure suivie comme "
                     "compétence et la relit avant les tâches semblables (SKILLS_AUTO).") else "0"
            env_values["SKILLS_MIN_TURNS"] = str(int(st.number_input(
                "SKILLS_MIN_TURNS", min_value=0, step=1, value=env_int("SKILLS_MIN_TURNS", 8),
                help="Complexité minimale (tours de l'agent) pour qu'une réussite devienne une compétence.")))
            env_values["CLAUDE_MODEL_SKILLS"] = st.text_input(
                "CLAUDE_MODEL_SKILLS", value=os.getenv("CLAUDE_MODEL_SKILLS", ""), placeholder="sonnet",
                help="Modèle de l'appel court qui rédige la compétence.").strip()
        env_values["AGENT_KNOWLEDGE_DIR"] = st.text_input(
            "AGENT_KNOWLEDGE_DIR", value=os.getenv("AGENT_KNOWLEDGE_DIR", ""),
            placeholder=r"\\serveur\partage\agent-savoye, X:\agent-savoye ou /mnt/partage/agent-savoye",
            help="Répertoire commun où chaque analyse est déposée en Markdown et consultée avant les suivantes. "
                 "Chemin UNC, lecteur mappé ou chemin POSIX. Vide = fonction désactivée. Tout utilisateur du partage "
                 "lit tous les documents.")
        with st.container(horizontal=True):
            env_values["AGENT_KNOWLEDGE_AUTHOR"] = st.text_input(
                "AGENT_KNOWLEDGE_AUTHOR", value=os.getenv("AGENT_KNOWLEDGE_AUTHOR", ""),
                placeholder=f"Vide = {knowledge_tools.author()}",
                help="Auteur inscrit dans les documents déposés. Vide = utilisateur système.")
            knowledge_max = st.number_input(
                "AGENT_KNOWLEDGE_MAX_CONTEXT", min_value=0, max_value=100000, step=1000,
                value=env_int("AGENT_KNOWLEDGE_MAX_CONTEXT", knowledge_tools.DEFAULT_MAX_CONTEXT),
                help="Taille maximale (caractères) des connaissances partagées injectées dans un prompt. 0 = aucune.")
        env_values["AGENT_KNOWLEDGE_MAX_CONTEXT"] = str(int(knowledge_max))

        st.markdown("---")
        submit_save = st.form_submit_button("💾 Enregistrer la configuration complète", type="primary", use_container_width=True)

        mcp_refused = ""
        if submit_save and env_values.get(mcp_servers.URL_ENV):
            # « Connect to MCP » collé tel quel : adresse et en-têtes non secrets extraits, jetons refusés
            connect = mcp_servers.parse_connect(env_values[mcp_servers.URL_ENV])
            if connect["erreur"]:
                mcp_refused = f"Adresse Groundcover refusée : {connect['erreur']}."
            else:
                env_values[mcp_servers.URL_ENV] = connect["url"]
                headers = {**connect["headers"], **mcp_servers.parse_headers(env_values[mcp_servers.HEADERS_ENV])}
                secret = mcp_servers.secret_headers(headers)
                env_values[mcp_servers.HEADERS_ENV] = mcp_servers.format_headers(
                    {k: v for k, v in headers.items() if k not in secret})
                if connect["refuses"] or secret:
                    st.warning("En-têtes d'authentification non enregistrés (aucun jeton dans le .env) : "
                               + ", ".join(connect["refuses"] + secret) + ". La connexion se fait par navigateur "
                               "(« Se connecter à Groundcover »).", icon=":material/key_off:")
        if mcp_refused:
            st.error(mcp_refused + " Configuration non enregistrée.", icon=":material/error:")
        elif submit_save:
            # Les secrets sont enregistrés tels quels ; les autres valeurs sans espaces superflus. Écriture unique
            # (une ligne par valeur, échappements, relecture contrôlée)
            try:
                env_tools.write_env(ENV_PATH, {
                    name: value if name.endswith(("_TOKEN", "_SECRET", "_PASSWORD", "_API_KEY")) else value.strip()
                    for name, value in env_values.items() if not config_commune.geree(name)})
            except ValueError as e:
                st.error(f"Configuration non enregistrée correctement : {e}.")
            else:
                st.success("Toute la configuration a été enregistrée avec succès dans le fichier .env !")
                st.rerun()

    # Tests de connexion (base, groundcover, MCP, répertoire partagé) : agent-projets (IHM 8504)
    st.link_button("Tests de connexion : Projets et connexions", os.getenv("AGENT_PROJETS_URL", "").strip()
                   or "http://localhost:8504", icon=":material/lan:")
    st.caption("Recherche, aperçu, statut des documents, import de .md existants et documents en attente : onglet "
               "📚 Connaissances partagées.")

    st.divider()
    st.subheader("Mémoire de l'agent")
    memories = get_all_memories()

    with st.container(horizontal=True):
        if st.button("Importer l'historique local", icon=":material/download:",
                     help="Ajoute les *-cadrage.md et *.patch présents dans le workspace"):
            count = import_existing_deliverables(os.path.dirname(os.path.abspath(__file__)))
            st.toast(f"{count} ticket(s) importé(s) en mémoire.")
            st.rerun()

    if not memories:
        st.info("La mémoire est vide.")
    else:
        st.dataframe(memories, hide_index=True, width="stretch")

        selected_mem = st.selectbox("Consulter un ticket mémorisé", [m["issue_key"] for m in memories])
        mem = load_ticket_memory(selected_mem)
        with st.expander(f"Détail de {selected_mem}"):
            for fb in mem.get("feedbacks", []):
                st.markdown(f"- `{fb.get('source')}` {fb.get('feedback')}")
                if fb.get("diff"):
                    st.code(fb["diff"], language="diff")
            st.markdown(mem.get("final_cadrage") or "_Pas de cadrage._")
        if st.button(f"Supprimer {selected_mem} de la mémoire", icon=":material/delete:"):
            delete_ticket_memory(selected_mem)
            st.rerun()

        with st.container(border=True):
            st.markdown("**Purger la mémoire**")
            scope = st.selectbox("Périmètre", ["Tous les projets"] + sorted({m["project_key"] for m in memories}))
            confirm = st.checkbox("Je confirme la suppression définitive")
            if st.button("Purger", type="primary", disabled=not confirm, icon=":material/delete_sweep:"):
                count = clear_memory("" if scope == "Tous les projets" else scope)
                st.toast(f"{count} ticket(s) supprimé(s) de la mémoire.")
                st.rerun()