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
    assert cfg.search.sources.linkedin.enabled and cfg.search.posted_within_days == 1
