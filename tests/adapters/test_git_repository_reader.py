import subprocess

from job_agent.adapters.code_repositories import GitRepositoryReader
from job_agent.domain.models import RepoRef


def _make_repo(path):
    path.mkdir()
    (path / "README.md").write_text("# Demo\nA FastAPI service")
    (path / "app.py").write_text("print('hi')\n" * 50)
    (path / "requirements.txt").write_text("fastapi\n")
    for cmd in (["init", "-q"], ["add", "."], ["-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qm", "i"]):
        subprocess.run(["git", *cmd], cwd=path, check=True)


def test_reads_any_git_remote(tmp_path):
    src = tmp_path / "src"
    _make_repo(src)
    reader = GitRepositoryReader(repositories=[RepoRef(url=str(src)), RepoRef(url=str(src) + ".git/")])
    repos = reader.list_repositories()
    assert len(repos) == 1  # de-duplicated
    head = reader.head(repos[0])
    evidence = reader.collect_evidence(repos[0])
    assert evidence.head == head and len(head) == 40
    assert "Python 100%" in evidence.summary and "FastAPI service" in evidence.summary
    assert "fastapi" in evidence.summary


def test_unreachable_repo_is_skipped(tmp_path):
    reader = GitRepositoryReader()
    assert reader.collect_evidence(RepoRef(url=str(tmp_path / "missing"))) is None
    assert reader.head(RepoRef(url=str(tmp_path / "missing"))) == ""
