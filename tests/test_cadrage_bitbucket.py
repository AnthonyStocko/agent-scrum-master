"""
Cadrage d'un ticket client : Standard et dépôt client lus dans Bitbucket (API REST, tools/bitbucket_code.py), sans
clone ni fetch ; repli local journalisé ; patch, socle seul (STD_ONLY_PROJECTS) inchangés. Données fictives.
"""
import importlib
import io
import subprocess
import sys

import pytest

BASE = "https://git.example"
REPOS = {"STDX": ["wms-core", "studio"], "CLIX": ["clix-app", "clix-ui"]}


def _main():
    saved = sys.stdout, sys.stderr
    sys.stdout, sys.stderr = io.StringIO(), io.StringIO()
    try:
        return importlib.import_module("main")
    finally:
        sys.stdout, sys.stderr = saved


class Response:
    def __init__(self, status, values=()):
        self.status_code, self.text = status, ""
        self._values = [{"slug": v} for v in values]

    def json(self):
        return {"values": self._values, "isLastPage": True}


def fake_http(status=200):
    def http(method, url, **kwargs):
        project = url.split("/projects/")[1].split("/")[0]
        return Response(status, REPOS.get(project, []))
    return http


@pytest.fixture
def env(monkeypatch):
    monkeypatch.setenv("BITBUCKET_URL", BASE)
    monkeypatch.setenv("BITBUCKET_HTTP_TOKEN", "jeton-fictif")
    monkeypatch.setenv("BITBUCKET_STD_REPO_PATH", f"{BASE}/projects/STDX")
    monkeypatch.setenv("STD_ONLY_PROJECTS", "SOCLEX")
    for name in ("BITBUCKET_CLIENT_REPO_PATH", "PROMPTS_DIR", "ODATIO_VERSION", "STD_BASE_BRANCHES"):
        monkeypatch.delenv(name, raising=False)
    return monkeypatch


# -- Choix du mode -------------------------------------------------------------------------------------------------

def test_plan_reads_both_repositories_in_bitbucket(env):
    plan = _main().cadrage_remote_plan("STDX", f"{BASE}/projects/CLIX/repos/clix-app/browse", http=fake_http())
    assert plan["remote"] and plan["client_project"] == "CLIX" and plan["client_repos"] == ["clix-app"]
    assert plan["std"].project == "STDX"


@pytest.mark.parametrize("unset, motif", [("BITBUCKET_URL", "BITBUCKET_URL"),
                                          ("BITBUCKET_HTTP_TOKEN", "BITBUCKET_HTTP_TOKEN")])
def test_plan_is_local_without_configuration(env, unset, motif):
    env.delenv(unset)
    plan = _main().cadrage_remote_plan("STDX", f"{BASE}/projects/CLIX", http=fake_http())
    assert not plan["remote"] and motif in plan["motif"]


def test_plan_is_local_when_the_client_is_not_resolved(env, tmp_path):
    main = _main()
    assert "BITBUCKET_STD_REPO_PATH" in main.cadrage_remote_plan("", f"{BASE}/projects/CLIX")["motif"]
    plan = main.cadrage_remote_plan("STDX", str(tmp_path), http=fake_http())
    assert not plan["remote"] and "sans adresse Bitbucket" in plan["motif"]


def test_plan_is_local_when_the_api_refuses_access(env):
    plan = _main().cadrage_remote_plan("STDX", f"{BASE}/projects/CLIX", http=fake_http(401))
    assert not plan["remote"] and "HTTP 401" in plan["motif"]


def test_plan_is_local_when_a_client_repository_is_missing(env):
    plan = _main().cadrage_remote_plan("STDX", f"{BASE}/scm/clix/absent.git", http=fake_http())
    assert not plan["remote"] and "absent" in plan["motif"]


def test_prompt_block_renders_without_checkout():
    text = _main().render_prompt("bitbucket_code_cadrage", std_project="STDX", std_ref_label="branche par défaut",
                                 std_cli="PY STD", client_project="CLIX", client_repos="tous les dépôts du projet",
                                 client_cli="PY CLI")
    assert "PY STD" in text and "PY CLI" in text and "integration" in text
    assert "checkout DEPOT" not in text and "${" not in text


# -- main() : étape lancée jusqu'à l'appel de Claude ---------------------------------------------------------------

class Launched(Exception):
    def __init__(self, prompt, add_dirs):
        super().__init__("appel de Claude")
        self.prompt, self.add_dirs = prompt, add_dirs


def run_step(env, tmp_path, capsys, step, project, client_repo):
    main = _main()
    calls = []
    env.chdir(tmp_path)
    env.setattr(main.bitbucket_std.RemoteStandard, "repos", lambda self: REPOS.get(self.project, []))

    def local_repo(repo_input, label, only=None):
        calls.append(label)
        path = tmp_path / f"{label}_copie"
        path.mkdir(exist_ok=True)
        return str(path)

    real_run = subprocess.run

    def guarded_run(cmd, *args, **kwargs):
        assert not ({"clone", "fetch"} & set(cmd if isinstance(cmd, list) else [])), cmd
        return real_run(cmd, *args, **kwargs)

    def launched(prompt, model, add_dirs, *args, **kwargs):
        raise Launched(prompt, add_dirs)

    env.setattr(subprocess, "run", guarded_run)
    env.setattr(main, "ensure_local_repo", local_repo)
    env.setattr(main, "run_claude", launched)
    env.setattr(main, "flush_knowledge_outbox", lambda: None)
    env.setattr(main, "get_full_jira_issue_context", lambda *a, **k: {})
    env.setattr(main, "get_capitalized_experience", lambda *a, **k: "")
    env.setattr(main, "consult_knowledge", lambda *a, **k: {"bloc": "", "documents": []})
    env.setattr(main, "build_db_block", lambda *a, **k: "")
    env.setattr(main, "odatio_docs_block", lambda *a, **k: "")
    env.setattr(main, "estimation_prompt", lambda *a, **k: "")
    env.setattr(main.jira_guard, "applies", lambda step: False)
    env.setattr(main.analysis_publication, "ensure_local",
                lambda issue: {"source": "fichier", "message": "", "texte": ""})
    argv = ["main.py", "--step", step, "--issue", f"{project}-12", "--project", project]
    env.setattr(sys, "argv", argv + (["--client-repo", client_repo] if client_repo else []))
    with pytest.raises(Launched) as launched_info:
        main.main()
    return launched_info.value, calls, capsys.readouterr().out


def test_cadrage_reads_both_repositories_in_bitbucket_without_local_copy(env, tmp_path, capsys):
    env.setenv("ODATIO_VERSION", "26.24")
    launched, calls, out = run_step(env, tmp_path, capsys, "cadrage", "CLIX", f"{BASE}/projects/CLIX")
    assert calls == []
    assert "--project STDX --branches origin/release/26.24,origin/v26.24" in launched.prompt
    assert ",HEAD" in launched.prompt
    assert "--project CLIX --branches origin/integration" in launched.prompt
    assert "std_copie" not in launched.prompt and "client_copie" not in launched.prompt
    assert "checkout DEPOT" not in launched.prompt and "branche de base sur le dépôt client est 'integration'" in \
        launched.prompt
    assert len(launched.add_dirs) == 1  # dossier de l'agent seul : aucune copie de code
    assert "[CODE CADRAGE] Standard : Bitbucket REST, projet STDX, version 26.24" in out
    assert "[CODE CADRAGE] Client : Bitbucket REST, projet CLIX, tous les dépôts du projet, branche integration" in out
    assert "Mode local" not in out


def test_cadrage_limits_the_client_to_its_repository(env, tmp_path, capsys):
    launched, calls, out = run_step(env, tmp_path, capsys, "cadrage", "CLIX", f"{BASE}/scm/clix/clix-app.git")
    assert calls == [] and "--branches origin/integration --repos clix-app" in launched.prompt
    assert "--project STDX --branches HEAD" in launched.prompt
    assert "projet STDX, branche par défaut de chaque dépôt" in out


def test_cadrage_without_bitbucket_keeps_local_copies(env, tmp_path, capsys):
    env.delenv("BITBUCKET_URL")
    launched, calls, out = run_step(env, tmp_path, capsys, "cadrage", "CLIX", f"{BASE}/projects/CLIX")
    assert calls == ["std", "client"]
    assert "[CODE CADRAGE] Mode local : BITBUCKET_URL non renseignée" in out
    assert "[CODE CADRAGE] Standard : local," in out and "[CODE CADRAGE] Client : local," in out
    assert "dans le dépôt client" in launched.prompt and "client_copie" in launched.prompt
    assert "bitbucket_code.py" not in launched.prompt


def test_patch_still_uses_local_copies(env, tmp_path, capsys):
    launched, calls, out = run_step(env, tmp_path, capsys, "patch", "CLIX", f"{BASE}/projects/CLIX")
    assert calls == ["std", "client"] and "[CODE CADRAGE]" not in out
    assert "bitbucket_code.py" not in launched.prompt


def test_std_only_project_is_unchanged(env, tmp_path, capsys):
    launched, calls, out = run_step(env, tmp_path, capsys, "cadrage", "SOCLEX", "")
    assert calls == [] and "[CODE CADRAGE]" not in out
    assert "checkout DEPOT" in launched.prompt and "Le code du Standard se lit UNIQUEMENT" in launched.prompt
    assert "Dépôt client (projet" not in launched.prompt
