from job_agent.adapters.job_sources import LinkedInMcpJobSource
from job_agent.adapters.notifications import ConsoleNotifier, TelegramNotifier
from job_agent.bootstrap import build_container, build_collector
from job_agent.config import load_config


def test_container_wires_configured_adapters(monkeypatch):
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "t")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "1")
    cfg = load_config()
    cfg.search.exclude_companies = ["Acme"]
    container = build_container(cfg)
    [source] = container.run_search_cycle.sources
    assert isinstance(source, LinkedInMcpJobSource) and source.server.args == ["mcp-server-linkedin@latest"]
    assert container.run_search_cycle.job_filter.exclude_companies == ("Acme",)
    assert isinstance(container.notifier, TelegramNotifier)
    assert isinstance(build_container(cfg, dry_run=True).notifier, ConsoleNotifier)

    cfg.search.sources.linkedin.enabled = False
    assert build_container(cfg).run_search_cycle.sources == []


def test_llm_provider_and_per_task_models(monkeypatch):
    from job_agent.adapters.llm import AnthropicStructuredModel, OpenRouterStructuredModel

    monkeypatch.delenv("JOB_AGENT_MODEL", raising=False)
    monkeypatch.setenv("OPENROUTER_API_KEY", "k")
    cfg = load_config()
    cfg.llm.provider = "openrouter"
    cfg.llm.model = "vendor/default-model"
    cfg.llm.models = {"tailor": "vendor/strong-model"}
    uc = build_container(cfg).run_search_cycle
    assert isinstance(uc.matcher.model, OpenRouterStructuredModel)
    assert uc.matcher.model.name == "openrouter/vendor/default-model"
    assert uc.selector.model is uc.matcher.model  # same model -> shared instance
    assert uc.tailor.model.name == "openrouter/vendor/strong-model"

    cfg.llm.provider, cfg.llm.model, cfg.llm.models = "anthropic", None, {}
    model = build_container(cfg).run_search_cycle.matcher.model
    assert isinstance(model, AnthropicStructuredModel) and model.name == "anthropic/claude-opus-5-5"


def test_build_collector_uses_firestore_without_creating_llm(monkeypatch):
    from types import SimpleNamespace
    import job_agent.bootstrap as bootstrap

    client = object()
    monkeypatch.setenv("FIRESTORE_PROJECT_ID", "personal-search")
    monkeypatch.setattr(bootstrap, "firestore", SimpleNamespace(Client=lambda **kw: client), raising=False)
    monkeypatch.setattr(bootstrap, "build_llm", lambda *args: (_ for _ in ()).throw(AssertionError("LLM used")))

    collector = build_collector(load_config())

    assert isinstance(collector.source, LinkedInMcpJobSource)
    assert collector.store.client is client


def test_build_collector_requires_firestore_project(monkeypatch):
    import pytest

    monkeypatch.delenv("FIRESTORE_PROJECT_ID", raising=False)
    with pytest.raises(ValueError, match="FIRESTORE_PROJECT_ID"):
        build_collector(load_config())


def test_build_collector_reports_invalid_credentials(monkeypatch):
    from types import SimpleNamespace
    from google.auth.exceptions import DefaultCredentialsError
    import job_agent.bootstrap as bootstrap
    import pytest

    monkeypatch.setenv("FIRESTORE_PROJECT_ID", "personal-search")
    def fail(**kwargs):
        raise DefaultCredentialsError("no ADC")
    monkeypatch.setattr(bootstrap, "firestore", SimpleNamespace(Client=fail), raising=False)

    with pytest.raises(ValueError, match="credenciales"):
        build_collector(load_config())


def test_collect_cli_reports_missing_plan_without_old_agent(monkeypatch, capsys):
    import job_agent.entrypoints.cli as cli

    class Collector:
        def execute(self):
            raise ValueError("No search plan")

    monkeypatch.setattr(cli, "build_collector", lambda cfg: Collector(), raising=False)
    monkeypatch.setattr(cli, "build_container", lambda *a, **kw: (_ for _ in ()).throw(AssertionError("old agent used")))

    assert cli.main(["collect"]) == 2
    assert "No search plan" in capsys.readouterr().err


def test_collect_cli_fails_when_some_postings_could_not_be_saved(monkeypatch):
    import job_agent.entrypoints.cli as cli
    from job_agent.domain.models import CollectionReport

    class Collector:
        def execute(self):
            return CollectionReport(errors=["save 101: unavailable"])

    monkeypatch.setattr(cli, "build_collector", lambda cfg: Collector(), raising=False)
    assert cli.main(["collect"]) == 1


def test_seed_search_plan_from_stored_profile(tmp_path, monkeypatch, profile):
    from datetime import datetime, timezone
    from types import SimpleNamespace
    import job_agent.entrypoints.cli as cli
    from job_agent.adapters.persistence.json_store import JsonProfileStore
    from job_agent.domain.models import StoredProfile

    cfg = load_config()
    cfg.storage.data_dir = str(tmp_path)
    JsonProfileStore(tmp_path / "profile.json").save(StoredProfile(
        profile=profile, built_at=datetime.now(timezone.utc),
    ))
    saved = []
    monkeypatch.setattr(cli, "load_config", lambda path: cfg)
    monkeypatch.setattr(cli, "build_collector", lambda c: SimpleNamespace(store=SimpleNamespace(save_plan=saved.append)), raising=False)

    assert cli.main(["seed-search-plan"]) == 0
    assert saved[0].search.queries[0].keywords == "Backend Engineer"
    assert saved[0].max_details_per_run == cfg.search.max_details_per_run


def test_seed_search_plan_requires_existing_profile(tmp_path, monkeypatch):
    from types import SimpleNamespace
    import job_agent.entrypoints.cli as cli

    cfg = load_config()
    cfg.storage.data_dir = str(tmp_path)
    monkeypatch.setattr(cli, "load_config", lambda path: cfg)
    monkeypatch.setattr(cli, "build_collector", lambda c: SimpleNamespace(store=object()), raising=False)

    assert cli.main(["seed-search-plan"]) == 2
