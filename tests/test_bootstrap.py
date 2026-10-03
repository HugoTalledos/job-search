import pytest

from job_agent.bootstrap import build_llm
from job_agent.config import load_config


def test_llm_provider_and_per_task_models(monkeypatch):
    from job_agent.adapters.llm import AnthropicStructuredModel, OpenRouterStructuredModel

    monkeypatch.delenv("JOB_AGENT_MODEL", raising=False)
    monkeypatch.setenv("OPENROUTER_API_KEY", "k")
    cfg = load_config()
    cfg.llm.provider = "openrouter"
    cfg.llm.model = "vendor/default-model"
    cfg.llm.models = {"tailor": "vendor/strong-model"}
    cache = {}
    matcher = build_llm(cfg.llm, "match", cache)
    assert isinstance(matcher, OpenRouterStructuredModel)
    assert matcher.name == "openrouter/vendor/default-model"
    assert build_llm(cfg.llm, "profile", cache) is matcher  # same model -> shared instance
    assert build_llm(cfg.llm, "tailor", cache).name == "openrouter/vendor/strong-model"

    cfg.llm.provider, cfg.llm.model, cfg.llm.models = "anthropic", None, {}
    model = build_llm(cfg.llm, "match")
    assert isinstance(model, AnthropicStructuredModel) and model.name == "anthropic/claude-opus-5-5"


@pytest.mark.parametrize("command", ["run", "profile", "test-notify", "collect", "seed-search-plan"])
def test_legacy_commands_are_gone(command, capsys):
    import job_agent.entrypoints.cli as cli
    with pytest.raises(SystemExit) as exc:
        cli.main([command])
    assert exc.value.code == 2


def test_bootstrap_only_builds_llms():
    import job_agent.bootstrap as bootstrap
    assert not hasattr(bootstrap, "build_container") and hasattr(bootstrap, "build_llm")
