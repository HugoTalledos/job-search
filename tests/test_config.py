import pytest
from pydantic import ValidationError

from job_agent.config import MatchingConfig, load_config


@pytest.mark.parametrize("threshold", [-1, 101])
def test_notification_threshold_must_be_within_score_range(threshold):
    with pytest.raises(ValidationError):
        MatchingConfig(min_score_to_notify=threshold)


def test_env_expansion_and_defaults(tmp_path, monkeypatch):
    monkeypatch.setenv("JOBS_KEY", "secret")
    path = tmp_path / "c.yaml"
    path.write_text("search:\n  sources:\n    linkedin:\n      env: {K: '${JOBS_KEY}'}\n")
    cfg = load_config(path)
    assert cfg.search.sources.linkedin.env == {"K": "secret"}
    assert cfg.matching.min_score_to_notify == 70


def test_repo_config_parses():
    cfg = load_config()
    assert cfg.search.sources.linkedin.enabled


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


def test_firebase_bucket_is_loaded_from_environment(monkeypatch):
    from job_agent.config import require_firebase_storage_bucket
    monkeypatch.setenv('FIREBASE_STORAGE_BUCKET', 'private-bucket')
    assert require_firebase_storage_bucket() == 'private-bucket'


@pytest.mark.parametrize('value', [None, '', ' ', 'gs://private-bucket', 'bucket/path'])
def test_firebase_bucket_must_be_configured_as_bare_bucket_name(monkeypatch, value):
    from job_agent.config import require_firebase_storage_bucket
    if value is None:
        monkeypatch.delenv('FIREBASE_STORAGE_BUCKET', raising=False)
    else:
        monkeypatch.setenv('FIREBASE_STORAGE_BUCKET', value)
    with pytest.raises(ValueError, match='FIREBASE_STORAGE_BUCKET'):
        require_firebase_storage_bucket()


def test_legacy_search_keys_warn_and_are_ignored(tmp_path, caplog):
    path = tmp_path / "config.yaml"
    path.write_text("search:\n  locations: [Remote]\n  exclude_companies: [Secreta]\n  max_queries: 4\n")
    cfg = load_config(path)
    assert cfg.search.max_queries == 4 and not hasattr(cfg.search, "locations")
    assert "seed-search-preferences" in caplog.text and "Secreta" not in caplog.text
    assert "locations" in caplog.text and "exclude_companies" in caplog.text
    assert cfg.search_budgets().max_queries == 4


def test_no_warning_without_legacy_keys(tmp_path, caplog):
    path = tmp_path / "config.yaml"
    path.write_text("search:\n  max_queries: 4\n")
    load_config(path)
    assert "seed-search-preferences" not in caplog.text


def test_legacy_preferences_read_from_old_yaml(tmp_path):
    from job_agent.config import legacy_search_preferences

    path = tmp_path / "old.yaml"
    path.write_text("search:\n  extra_keywords: [Django]\n  locations: [Remote]\n  posted_within_days: 2\n")
    prefs = legacy_search_preferences(path)
    assert prefs.keywords_include == ["Django"] and prefs.locations == ["Remote"]


def test_legacy_preferences_require_some_legacy_key(tmp_path):
    from job_agent.config import legacy_search_preferences

    path = tmp_path / "new.yaml"
    path.write_text("search:\n  max_queries: 4\n")
    with pytest.raises(ValueError):
        legacy_search_preferences(path)
