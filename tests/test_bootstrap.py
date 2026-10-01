from job_agent.adapters.job_sources import McpJobSource, WebSearchJobSource
from job_agent.adapters.notifications import ConsoleNotifier, TelegramNotifier
from job_agent.bootstrap import build_container
from job_agent.config import load_config


def test_container_wires_configured_adapters(monkeypatch):
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "t")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "1")
    cfg = load_config()
    cfg.search.web_search = True
    container = build_container(cfg)
    sources = container.run_search_cycle.sources
    assert isinstance(sources[0], McpJobSource) and sources[0].name == "linkedin"
    assert isinstance(sources[-1], WebSearchJobSource)
    assert isinstance(container.notifier, TelegramNotifier)
    assert isinstance(build_container(cfg, dry_run=True).notifier, ConsoleNotifier)
