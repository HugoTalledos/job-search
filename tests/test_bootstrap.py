from job_agent.adapters.job_sources import LinkedInMcpJobSource
from job_agent.adapters.notifications import ConsoleNotifier, TelegramNotifier
from job_agent.bootstrap import build_container
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
