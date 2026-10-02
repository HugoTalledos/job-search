from pathlib import Path

from job_agent.config import load_config


def test_env_expansion_and_defaults(tmp_path, monkeypatch):
    monkeypatch.setenv("JOBS_KEY", "secret")
    path = tmp_path / "c.yaml"
    path.write_text("search:\n  sources:\n    linkedin:\n      env: {K: '${JOBS_KEY}'}\n")
    cfg = load_config(path)
    assert cfg.search.sources.linkedin.env == {"K": "secret"}
    assert cfg.matching.min_score_to_notify == 70


def test_repo_config_parses():
    cfg = load_config()
    assert cfg.search.sources.linkedin.enabled and cfg.search.posted_within_days == 2


def test_load_dotenv(tmp_path, monkeypatch):
    from job_agent.config import load_dotenv

    env = tmp_path / ".env"
    env.write_text("# comment\nA_KEY=one\nexport B_KEY='two words'\nC_KEY=\"3\"\nEXISTING=new\nbroken line\nBLANK_KEY=\n")
    monkeypatch.setenv("EXISTING", "old")
    for k in ("A_KEY", "B_KEY", "C_KEY", "BLANK_KEY"):
        monkeypatch.delenv(k, raising=False)
    load_dotenv(env)
    import os

    assert (os.environ["A_KEY"], os.environ["B_KEY"], os.environ["C_KEY"]) == ("one", "two words", "3")
    assert os.environ["EXISTING"] == "old"  # real environment wins
    assert "BLANK_KEY" not in os.environ  # blank placeholders stay unset
    load_dotenv(tmp_path / "missing.env")  # no error


def test_storage_paths(tmp_path):
    from job_agent.config import ROOT, StorageConfig

    default = StorageConfig()
    assert default.data_path == ROOT / "data" and default.output_path == ROOT / "output"
    custom = StorageConfig(data_dir=str(tmp_path / "d"), output_dir="~/job-search-cvs")
    assert custom.data_path == tmp_path / "d"
    assert custom.output_path == Path("~/job-search-cvs").expanduser()
