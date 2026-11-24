"""
Consultations en LECTURE SEULE pour agent-manager-savoye (serveur MCP ``mcp_lecture.py``).

Le manager s'en sert pour répondre à une question rapide (« où en est l'analyse de ODA-1234 ? », « un job Cucumber
tourne-t-il ? ») sans ouvrir l'IHM. Rien ici n'écrit : ni Jira, ni Bitbucket, ni Jenkins, ni fichier de l'agent.
Tout le reste (cadrage, chiffrage, patch, Cucumber…) se fait dans l'IHM, vers laquelle le manager redirige.
"""
import glob
import os
import re
from datetime import datetime

import requests

from tools import analysis_publication, assistant, cucumber_lock
from agent_socle import livraisons
from agent_socle.jenkins_tools import recent_builds
from agent_socle.secret_tools import mask_secrets

WORKSPACE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CLE = re.compile(r"^[A-Z][A-Z0-9_]+-\d+$")
JOBS = ("JENKINS_CUCUMBER_JOB", "CUCUMBER_VERIFY_JOB", "CUCUMBER_RUN_JOB")
ANALYSES_MAX = 30
PATCHS_JOURS_MAX = 90


class LectureErreur(Exception):
    pass


def _masque(texte: str) -> str:
    return mask_secrets(texte)[0]


def _cle(ticket: str) -> str:
    cle = (ticket or "").strip().upper()
    if not CLE.match(cle):
        raise LectureErreur(f"Clé de ticket invalide : « {ticket} » (format attendu : PROJET-123).")
    return cle


def _date(horodatage: float) -> str:
    return datetime.fromtimestamp(horodatage).strftime("%Y-%m-%d %H:%M")


def analyses(racine: str = WORKSPACE_DIR) -> str:
    """Analyses (cadrages) présentes dans l'agent, de la plus récente à la plus ancienne, avec leur état."""
    fichiers = sorted(glob.glob(os.path.join(racine, "*-cadrage.md")), key=os.path.getmtime, reverse=True)
    if not fichiers:
        return "Aucune analyse dans l'agent Scrum Master."
    lignes = []
    for chemin in fichiers[:ANALYSES_MAX]:
        cle = os.path.basename(chemin)[:-len("-cadrage.md")]
        with open(chemin, encoding="utf-8", errors="replace") as f:
            etat = analysis_publication.status(cle, f.read())
        a_valider = "" if etat["publiee"] else " ← à valider"
        lignes.append(f"- {cle} (modifiée le {_date(os.path.getmtime(chemin))}) : {etat['libelle']}{a_valider}")
    if len(fichiers) > ANALYSES_MAX:
        lignes.append(f"… {len(fichiers) - ANALYSES_MAX} analyse(s) plus ancienne(s)")
    return "Analyses de l'agent Scrum Master :\n" + "\n".join(lignes)


def analyse(ticket: str, racine: str = WORKSPACE_DIR) -> str:
    """État de l'analyse d'un ticket et ses titres de sections."""
    cle = _cle(ticket)
    chemin = os.path.join(racine, analysis_publication.cadrage_path(cle))
    if not os.path.exists(chemin):
        return f"Aucune analyse de {cle} dans l'agent Scrum Master."
    with open(chemin, encoding="utf-8", errors="replace") as f:
        texte = f.read()
    etat = analysis_publication.status(cle, texte)
    titres = [l.lstrip("#").strip() for l in texte.splitlines() if l.startswith("#")][:20]
    return _masque(f"Analyse de {cle} (modifiée le {_date(os.path.getmtime(chemin))}) : {etat['libelle']}.\n"
                   f"Sections : {' · '.join(titres) or '(aucun titre)'}")


def ticket(cle: str) -> str:
    """Titre, type, statut, responsable et épopée d'un ticket Jira (lecture ; compte de lecture s'il existe)."""
    cle = _cle(cle)
    domaine = os.getenv("JIRA_DOMAIN", "").replace("https://", "").replace("http://", "").strip("/")
    lecture = os.getenv("JIRA_READ_API_TOKEN", "").strip()
    email = (os.getenv("JIRA_READ_EMAIL") if lecture else os.getenv("JIRA_EMAIL", "")) or ""
    jeton = lecture or os.getenv("JIRA_API_TOKEN", "").strip()
    if not (domaine and email.strip() and jeton):
        raise LectureErreur("Jira n'est pas configuré dans l'agent Scrum Master.")
    try:
        reponse = requests.get(f"https://{domaine}/rest/api/3/issue/{cle}", auth=(email.strip(), jeton), timeout=15,
                               headers={"Accept": "application/json"},
                               params={"fields": "summary,status,issuetype,assignee,parent,updated"})
    except requests.RequestException as e:
        raise LectureErreur(f"Jira injoignable : {_masque(str(e))[:200]}")
    if reponse.status_code != 200:
        raise LectureErreur(f"Ticket {cle} illisible dans Jira (HTTP {reponse.status_code}).")
    champs = reponse.json().get("fields", {})
    parent = champs.get("parent") or {}
    lignes = [f"{cle} : {champs.get('summary', '')}",
              f"Type : {(champs.get('issuetype') or {}).get('name', '')} · Statut : "
              f"{(champs.get('status') or {}).get('name', '')} · Responsable : "
              f"{(champs.get('assignee') or {}).get('displayName', 'non assigné')} · Mis à jour : "
              f"{str(champs.get('updated', ''))[:16].replace('T', ' ')}"]
    if parent:
        lignes.append(f"Épopée : {parent.get('key')} {(parent.get('fields') or {}).get('summary', '')}")
    return _masque("\n".join(lignes) + "\n" + analyse(cle))


def cucumber() -> str:
    """Clients Cucumber connus de l'agent et génération en cours pour chacun (verrou par client)."""
    clients = assistant.known_clients()
    clients = [c for c in clients if not c.startswith("_")]
    if not clients:
        return "Aucun client Cucumber dans l'agent Scrum Master."
    lignes = []
    for client in clients:
        detenteur = cucumber_lock.holder(client)
        lignes.append(f"- {client} : {cucumber_lock.describe(detenteur) if detenteur else 'aucune génération en cours'}")
    jobs = [f"{n} = {os.getenv(n)}" for n in JOBS if os.getenv(n, "").strip()]
    return _masque("Cucumber dans l'agent Scrum Master :\n" + "\n".join(lignes)
                   + ("\nJobs Jenkins : " + ", ".join(jobs) if jobs else "\nAucun job Jenkins configuré."))


def builds(job: str = "", nombre: int = 5) -> str:
    """Derniers builds des jobs Cucumber configurés (un seul build Cucumber à la fois sur Jenkins)."""
    configures = [os.getenv(n, "").strip() for n in JOBS if os.getenv(n, "").strip()]
    configures = list(dict.fromkeys(configures))
    if job and job not in configures:
        raise LectureErreur(f"Job inconnu de l'agent : « {job} » (jobs configurés : {', '.join(configures) or 'aucun'}).")
    nombre = max(1, min(int(nombre or 5), 20))
    blocs = []
    for nom in [job] if job else configures:
        lignes = []
        for b in recent_builds(nom, nombre):
            debut = _date(b["debut"] / 1000) if b.get("debut") else "?"
            resultat = b.get("resultat") or "EN COURS"
            lignes.append(f"  #{b['numero']} {resultat} — {debut}, {round((b.get('duree') or 0) / 60000)} min")
        blocs.append(f"{nom} :\n" + ("\n".join(lignes) or "  (aucun build lisible)"))
    return _masque("\n".join(blocs) or "Aucun job Jenkins configuré dans l'agent Scrum Master.")


def patchs(jours: int = 7, chemin: str = "") -> str:
    """Patchs livrés ces derniers jours, d'après le registre local des livraisons écrit par l'étape patch.

    Format (contrat avec agent-manager-savoye, une ligne par livraison, de la plus récente à la plus ancienne) :
    ``- <CLÉ> (livré le AAAA-MM-JJ HH:MM) : <fichier.patch>[, …] — <état>`` où l'état est
    ``en pièce jointe Jira``, ``NON joint dans Jira : <fichiers>`` ou ``pièce jointe non vérifiée (<raison>)``.
    """
    try:
        jours = max(1, min(int(jours or 7), PATCHS_JOURS_MAX))
    except (TypeError, ValueError):
        raise LectureErreur(f"Nombre de jours invalide : « {jours} ».")
    try:
        entrees = livraisons.lire(jours, chemin or livraisons.FICHIER)
    except livraisons.RegistreErreur as e:
        raise LectureErreur(str(e))
    if not entrees:
        return (f"Aucun patch livré ces {jours} derniers jours dans l'agent Scrum Master (registre des livraisons, "
                f"rempli à chaque étape patch).")
    lignes = []
    for e in entrees:
        manquants = [f for f in e["fichiers"] if f not in e["pieces_jointes"]]
        if e["verification"] != "ok":
            etat = f"pièce jointe non vérifiée ({e['verification'] or 'raison inconnue'})"
        elif manquants:
            etat = "NON joint dans Jira : " + ", ".join(manquants)
        else:
            etat = "en pièce jointe Jira"
        lignes.append(f"- {e['ticket']} (livré le {e['date'][:16].replace('T', ' ')}) : {', '.join(e['fichiers'])}"
                      f" — {etat}")
    return _masque(f"Patchs livrés ces {jours} derniers jours (agent Scrum Master) :\n" + "\n".join(lignes))
