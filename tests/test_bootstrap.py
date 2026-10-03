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


def _cli_with_fake_seed(monkeypatch, created=True, plan_written=True):
    import job_agent.entrypoints.cli as cli
    from job_agent.application.preference_models import SeedResult

    result = SeedResult(created=created, plan_written=plan_written)

    calls = {}

    class Fake:
        def seed(self, preferences, *, force):
            calls["seed"] = (preferences, force)
            return result

    def factory(budgets):
        calls["budgets"] = budgets
        return Fake()

    monkeypatch.setattr(cli, "build_search_preferences_manager", factory)
    return cli, calls


def test_cli_seed_reads_from_config_path(monkeypatch, tmp_path, capsys):
    cli, calls = _cli_with_fake_seed(monkeypatch)
    path = tmp_path / "old.yaml"
    path.write_text("search:\n  extra_keywords: [Django]\n  locations: [Remote]\n")
    active = tmp_path / "config.yaml"
    active.write_text("search:\n  max_queries: 5\n")
    assert cli.main(["--config", str(active), "seed-search-preferences", "--from-config", str(path), "--force"]) == 0
    prefs, force = calls["seed"]
    assert prefs.keywords_include == ["Django"] and force is True
    assert calls["budgets"].max_queries == 5
    out = capsys.readouterr().out
    assert "Preferencias publicadas en Firestore" in out and "No se generó un plan" not in out


def test_cli_seed_reports_when_no_plan_was_compiled(monkeypatch, tmp_path, capsys):
    cli, _ = _cli_with_fake_seed(monkeypatch, plan_written=False)
    path = tmp_path / "old.yaml"
    path.write_text("search:\n  locations: [Remote]\n")
    assert cli.main(["seed-search-preferences", "--from-config", str(path)]) == 0
    out = capsys.readouterr().out
    assert "Preferencias publicadas en Firestore" in out
    assert ("No se generó un plan de búsqueda: no hay palabras clave ni perfil; se mantiene el plan anterior."
            in out)


def test_cli_seed_reports_existing_preferences(monkeypatch, tmp_path, capsys):
    cli, _ = _cli_with_fake_seed(monkeypatch, created=False, plan_written=False)
    path = tmp_path / "old.yaml"
    path.write_text("search:\n  locations: [Remote]\n")
    assert cli.main(["seed-search-preferences", "--from-config", str(path)]) == 0
    assert "Ya existen preferencias; usa --force para reemplazarlas" in capsys.readouterr().out


def test_cli_seed_without_legacy_keys_is_a_config_error(monkeypatch, tmp_path, capsys):
    cli, calls = _cli_with_fake_seed(monkeypatch)
    path = tmp_path / "new.yaml"
    path.write_text("search:\n  max_queries: 4\n")
    assert cli.main(["seed-search-preferences", "--from-config", str(path)]) == 2
    assert "Error de configuración" in capsys.readouterr().err and "seed" not in calls
