import subprocess

from job_agent.config import RepoSpec
from job_agent.repos import digest_repo


def test_digest_local_repo(tmp_path):
    src = tmp_path / "src"
    src.mkdir()
    (src / "README.md").write_text("# Demo\nA FastAPI service")
    (src / "app.py").write_text("print('hi')\n" * 50)
    (src / "requirements.txt").write_text("fastapi\n")
    for cmd in (["init", "-q"], ["add", "."], ["-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qm", "i"]):
        subprocess.run(["git", *cmd], cwd=src, check=True)
    digest = digest_repo(RepoSpec(url=str(src)))
    assert digest and len(digest.head) == 40
    assert "Python 100%" in digest.text and "FastAPI service" in digest.text and "fastapi" in digest.text
