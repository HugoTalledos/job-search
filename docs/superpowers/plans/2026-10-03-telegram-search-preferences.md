# Telegram Search Preferences Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Retire the legacy `job_agent run` flow, move search preferences from `config.yaml` to Firestore, and let the candidate view them deterministically with `/preferencias` and change them in natural language with `/preferencias <texto>` plus an Apply/Cancel confirmation; every change recompiles the plan the local collector reads.

**Architecture:** `settings/search_preferences` (a shared `job_contracts` model) is the source of truth. A pure `build_search_plan` in the agent domain compiles preferences + profile + budgets into `settings/search_plan`, which the collector keeps reading and now also uses to drop excluded companies/titles. A use case `ManageSearchPreferences` coordinates an LLM interpreter (returns edit operations, never a whole document), drafts in Firestore and a transactional apply; the Telegram entrypoint only parses updates and calls it.

**Tech Stack:** Python 3.10+, FastAPI/Starlette, Pydantic 2, Google Cloud Firestore, httpx, OpenRouter structured output, pytest.

**Spec:** `docs/superpowers/specs/2026-10-03-telegram-search-preferences-design.md`

**Base:** `origin/main` at `38f1609`. Run every command from the repository root; tests use `OPENROUTER_API_KEY=test .venv/bin/pytest`. Do not commit anything under `docs/superpowers/` (ignored by the owner's rules).

## Global Constraints

- `/preferencias` with no text is purely deterministic: no LLM call, no draft, no background task; it answers within the same request.
- `/preferencias`, its buttons and every other command require the webhook secret and a private chat whose `chat.id` and `from.id` equal `TELEGRAM_CHAT_ID`.
- Nothing changes until **✅ Aplicar**; applying writes preferences (version + 1), the recompiled plan and the draft status in one Firestore transaction, or nothing.
- Never publish a plan with zero queries; with no keywords the preferences are saved and the previous plan is kept.
- Drafts expire after 24 hours; a draft whose `base_version` differs from the current version is never applied.
- The LLM returns edit operations only; it must not invent company names the candidate did not write. Fields not mentioned stay untouched.
- Migrated to Firestore: `extra_keywords`→`keywords_include`, `locations`, `posted_within_days`, `work_types`, `experience_levels`, `exclude_companies`, `exclude_title_keywords`. Stay in `config.yaml`: `search.sources.linkedin`, `search.max_queries`, `search.max_roles_from_profile`, `search.max_details_per_run`, `matching.min_score_to_notify`, `llm`, `resume_path`, `github_*`, `repositories`, `profile_refresh_days`, `language`.
- `work_types` ⊆ {`remote`, `hybrid`, `on_site`}; `experience_levels` ⊆ {`internship`, `entry`, `associate`, `mid_senior`, `director`, `executive`}; `posted_within_days` in 1–30.
- Logs never include the candidate's free text or LLM output; only exception type names and IDs.
- The collector keeps depending only on `job_contracts` (never on `job_agent`). Tests never need real Telegram, LLM or Firebase.
- The webhook (`job_agent.webhook:app`) and the collector must keep working after every task.

## File Structure

| File | Responsibility |
|---|---|
| legacy-only modules (Task 1) | Deleted: `RunSearchCycle`, `build_container`, agent job sources, JSON state/history/catalogue stores, resume selector, console notifier, alert formatting, legacy policies, `run_local.sh`. |
| `job_contracts/normalize.py` (new) | Company/title normalisation moved from `job_agent/domain/policies.py`, plus `exclusion_reason`. |
| `job_contracts/preferences.py` (new) | `SearchPreferences` document model with validation and canonicalisation. |
| `job_contracts/models.py`, `__init__.py` | `CollectorPlan` exclusion/provenance fields; `CollectionReport.excluded`. |
| `local_collector/collect_jobs.py` | Drop excluded postings before saving. |
| `job_agent/domain/policies.py` | `SearchBudgets`, `build_search_plan`, `profile_fingerprint`. |
| `job_agent/domain/preference_edits.py` (new) | `PreferenceOperation`, `PreferenceEdit`, pure `apply_operations`, `preference_diff`. |
| `job_agent/application/ports.py` | `SearchSettingsStore`, `PreferenceInterpreter` ports. |
| `job_agent/application/preference_models.py` (new) | Draft, proposal, resolution and view value objects. |
| `job_agent/application/manage_search_preferences.py` (new) | Use case: seed, show, propose, resolve, rebuild_plan. |
| `job_agent/application/build_profile.py` | Profile locations come from a provider read at inference time. |
| `job_agent/adapters/persistence/firestore_search_settings.py` (new) | Preferences, plan and drafts in Firestore with transactional apply/seed. |
| `job_agent/adapters/llm/prompts.py`, `tasks.py` | `LlmPreferenceInterpreter`. |
| `job_agent/adapters/notifications/telegram_preferences.py` (new) | Formatting and inline-keyboard messages, `editMessageText`, `answerCallbackQuery`. |
| `job_agent/entrypoints/telegram.py`, `cli.py` | `/preferencias`, callback queries, plan rebuild after `/build_profile`; `seed-search-preferences`. |
| `job_agent/config.py`, `bootstrap.py`, `webhook.py` | Remove legacy and migrated keys (warn on migrated ones), wire stores and use case. |
| `adapters/notifications/telegram_profile.py`, `config.yaml`, `README.md`, `local_collector/README.md`, `scripts/macos/install_schedule.py` | Menu, `allowed_updates`, config cleanup, collector-only schedule, operator migration. |

## Review Focus

1. Retiring the legacy flow must not remove anything the webhook or the collector imports; pin in Task 1 with a webhook import/start test and the full suite.
2. A legacy `config.yaml` that still has migrated keys must start with a warning, not crash, and must not silently override Firestore; pin in Task 4.
3. Two taps on **Aplicar**, or Apply after another draft was applied, must apply at most once and never with a stale base; pin in Task 3 with the transactional fake.
4. A malformed LLM answer (unknown field, `add` on a scalar, invalid enum, `posted_within_days` = "40") must yield an explanation and no draft; pin in Task 5.
5. A candidate's text containing `<`, `&` or HTML must not break Telegram HTML messages, and a `callback_query` from another chat/user, with malformed `data`, or for an unknown draft must change nothing and still be answered; pin in Task 7.

---

### Task 1: Retire the legacy `job_agent run` flow

**Files:**
- Delete: `job_agent/application/run_search_cycle.py`, `job_agent/adapters/job_sources/` (whole package), `job_agent/adapters/persistence/json_store.py`, `job_agent/adapters/notifications/console_notifier.py`, `scripts/macos/run_local.sh`, and every test module that only exercises deleted code (e.g. `tests/application/test_run_search_cycle.py`; check each candidate with `grep` before deleting).
- Modify: `job_agent/bootstrap.py` (keep only `build_llm` and its imports), `job_agent/entrypoints/cli.py`, `job_agent/application/__init__.py`, `job_agent/adapters/*/__init__.py`, `job_agent/adapters/llm/tasks.py` and `prompts.py` (remove `LlmResumeSelector` and select prompts), `job_agent/adapters/notifications/telegram_notifier.py` (keep `TelegramNotifier.__init__`, `_api`, `send_text`; remove `notify`, `format_message`, `_resume_line`), `job_agent/adapters/resume/markdown_renderer.py` (remove `write_outputs` only if `pdf_renderer.py` does not use it), `job_agent/domain/policies.py` (remove `SearchPreferences`, `JobFilter`, `MatchingPolicy`, `ReusePolicy` and helpers used only by them), `job_agent/domain/models.py` (remove models used only by deleted code), `job_agent/config.py` (remove `StorageConfig`, `ResumeReuseConfig`, `MatchingConfig.min_score_to_tailor`, `SearchConfig.max_jobs_per_run`), `config.yaml` (remove `storage`, `resume_reuse`, `matching.min_score_to_tailor`, `search.max_jobs_per_run`), `scripts/macos/install_schedule.py`, `README.md`, `tests/fakes.py`, `tests/test_schedule.py`, `tests/test_bootstrap.py`, `tests/test_config.py`.
- Keep (explicit keep-list, even if a grep looks thin): `build_llm`, `EnsureProfile`, `BuildProfessionalProfile`, `GenerateTailoredCv`, `LoadCollectedJobs` and `firestore_postings.py` (not part of the legacy flow; out of scope), `LlmJobMatcher`, `LlmResumeTailor`, `LlmProfileInferer`, `GitRepositoryReader`, `FileResumeSource`, `RequiredPdfRenderer` and whatever it imports, `TelegramNotifier.send_text`, `TELEGRAM_LIMIT`, `normalize_company`, `normalize_title`, `_norm`, `profile_changes`, `resume_fingerprint`, `repos_fingerprint`, `ProfileRefreshPolicy`, everything under `job_agent/scoring/`.

**Interfaces:**
- Produces: CLI `job_agent` with only `set-telegram-webhook` (Task 4 adds `seed-search-preferences`). `install_schedule.py` keeps `--component` with the single choice `collector` (default), drops `run_local.sh`, the `legacy` label and its preflight; `default_label()` returns the collector label.
- Ruling: `job_agent.webhook` imports must not change in this task except removed re-exports; if a kept module imports a deleted symbol, stop and report BLOCKED instead of widening the deletion.

- [ ] **Step 1: Write failing tests.**

```python
@pytest.mark.parametrize("command", ["run", "profile", "test-notify", "collect", "seed-search-plan"])
def test_legacy_commands_are_gone(command, capsys):
    import job_agent.entrypoints.cli as cli
    with pytest.raises(SystemExit) as exc:
        cli.main([command])
    assert exc.value.code == 2

def test_bootstrap_only_builds_llms():
    import job_agent.bootstrap as bootstrap
    assert not hasattr(bootstrap, "build_container") and hasattr(bootstrap, "build_llm")

def test_schedule_installs_only_the_collector():
    plist = install_schedule.build_plist(install_schedule.default_label(), [{"Hour": 8, "Minute": 0}])
    assert plist["ProgramArguments"][-1].endswith("run_collector.sh")
```

  Adapt the existing schedule test that parametrises `run_local.sh` to the collector only. Keep `tests/test_webhook_bootstrap.py` unchanged; it is the guard that the webhook still starts.
- [ ] **Step 2: Run red:** `OPENROUTER_API_KEY=test .venv/bin/pytest -q tests/test_bootstrap.py tests/test_schedule.py` — expect the legacy commands to still parse.
- [ ] **Step 3: Delete and trim.** Remove the CLI commands and `build_container` first, then delete modules and symbols that become unreferenced (search `job_agent`, `local_collector`, `scripts`, `tests`), respecting the keep-list. Update the README: remove the "Flujo anterior (`job_agent run`)" section and every instruction that runs it, and add a short note to uninstall the old launchd job if it was installed: `launchctl bootout gui/$(id -u)/<label>.job-search` and delete its plist under `~/Library/LaunchAgents/`.
- [ ] **Step 4: Run green:** `OPENROUTER_API_KEY=test .venv/bin/pytest -q` (all pass, including `tests/test_architecture.py` and `tests/test_webhook_bootstrap.py`); `.venv/bin/python -m compileall -q job_agent job_contracts local_collector tests`; `grep -rn "run_local\|build_container\|RunSearchCycle" job_agent local_collector scripts tests README.md` returns nothing. (Do not import `job_agent.webhook` directly: it builds the app from the environment; `tests/test_webhook_bootstrap.py` covers it.)
- [ ] **Step 5: Commit:** `git add -A job_agent tests scripts config.yaml README.md` then `git commit -m "Retire the legacy job_agent run flow"`.

### Task 2: Shared preferences contract and collector exclusions

**Files:**
- Create: `job_contracts/normalize.py`, `job_contracts/preferences.py`, `tests/contracts/__init__.py`, `tests/contracts/test_preferences.py`
- Modify: `job_contracts/models.py`, `job_contracts/__init__.py`, `job_agent/domain/policies.py` (import normalisation from contracts), `local_collector/collect_jobs.py`
- Test: `tests/contracts/test_preferences.py`, `tests/application/test_collect_jobs.py`, `tests/domain/test_policies.py` (must still pass)

**Interfaces:**
- Produces in `job_contracts.normalize`: `normalize_company(name: str) -> str`, `normalize_title(title: str) -> str`, `normalize_keyword(text: str) -> str` (the current `_norm`), `exclusion_reason(job: JobPosting, exclude_companies: Sequence[str], exclude_title_keywords: Sequence[str]) -> Literal["empresa_excluida", "palabra_excluida_en_titulo"] | None` (company equality after `normalize_company`; title match as whole normalised words, like the former `JobFilter`). Move the suffix/abbreviation/noise tables verbatim; `job_agent.domain.policies` re-exports `normalize_company`, `normalize_title` and keeps `_norm = normalize_keyword` if still used.
- Produces in `job_contracts.preferences`:

```python
WorkType = Literal["remote", "hybrid", "on_site"]
ExperienceLevel = Literal["internship", "entry", "associate", "mid_senior", "director", "executive"]

class SearchPreferences(BaseModel):
    model_config = ConfigDict(extra="ignore")
    keywords_include: list[str] = []
    keywords_exclude: list[str] = []
    use_profile_keywords: bool = True
    locations: list[str] = []
    posted_within_days: int = Field(default=2, ge=1, le=30)
    work_types: list[WorkType] = []
    experience_levels: list[ExperienceLevel] = []
    exclude_companies: list[str] = []
    exclude_title_keywords: list[str] = []
    version: int = Field(default=0, ge=0)
    updated_at: datetime | None = None

    def content_equals(self, other: "SearchPreferences") -> bool: ...  # everything except version/updated_at
```

  An `after` validator strips every string, drops empties and removes duplicates by `normalize_keyword` (companies by `normalize_company`), keeping the first occurrence.
- Produces in `job_contracts.models`: `CollectorPlan` gains `exclude_companies: list[str] = []`, `exclude_title_keywords: list[str] = []`, `preferences_version: int = 0`, `profile_fingerprint: str = ""`, `built_at: datetime | None = None`; `CollectionReport` gains `excluded: int = 0`. Export `SearchPreferences` from `job_contracts`.

- [ ] **Step 1: Write failing tests.**

```python
def test_preferences_canonicalise_lists():
    prefs = SearchPreferences(keywords_include=[" Python ", "python", ""], exclude_companies=["Acme Inc.", "ACME"])
    assert prefs.keywords_include == ["Python"]
    assert prefs.exclude_companies == ["Acme Inc."]

@pytest.mark.parametrize("bad", [{"work_types": ["office"]}, {"experience_levels": ["senior"]}, {"posted_within_days": 0}, {"posted_within_days": 31}])
def test_preferences_reject_values_linkedin_does_not_accept(bad):
    with pytest.raises(ValidationError):
        SearchPreferences(**bad)

def test_old_plan_without_exclusions_still_loads():
    plan = CollectorPlan.model_validate({"search": {"queries": [{"keywords": "backend"}], "posted_within_days": 2}, "max_details_per_run": 5})
    assert plan.exclude_companies == [] and plan.preferences_version == 0

def test_collector_drops_excluded_company_and_title_before_saving(job):
    plan = _plan(max_details=5).model_copy(update={"exclude_companies": ["ACME"], "exclude_title_keywords": ["intern"]})
    store = MemoryStore(plan)
    jobs = [job.model_copy(update={"external_id": "1", "company": "Acme Inc."}),
            job.model_copy(update={"external_id": "2", "title": "Backend Intern"}),
            job.model_copy(update={"external_id": "3"})]
    report = CollectJobs(MemorySource(jobs, store), store).execute()
    assert [j.external_id for j in store.jobs.values()] == ["3"]
    assert (report.fetched, report.excluded, report.inserted) == (3, 2, 1)
```

  Also assert `exclusion_reason` returns `None` for empty exclusion lists.
- [ ] **Step 2: Run red:** `OPENROUTER_API_KEY=test .venv/bin/pytest -q tests/contracts tests/application/test_collect_jobs.py` — expect import errors and the missing `excluded` count.
- [ ] **Step 3: Implement.** Move normalisation (no behaviour change), add the model and fields, and in `CollectJobs.execute` call `exclusion_reason(job, plan.exclude_companies, plan.exclude_title_keywords)` before `store.save`; excluded jobs are counted and not saved. Log only counts.
- [ ] **Step 4: Run green:** the command above plus `tests/domain tests/test_architecture.py`; expect zero failures.
- [ ] **Step 5: Commit:** `git add job_contracts local_collector/collect_jobs.py job_agent/domain/policies.py tests/contracts tests/application/test_collect_jobs.py` then `git commit -m "Share search preferences contract and drop excluded postings"`.

### Task 3: Plan compiler and Firestore search settings

**Files:**
- Create: `job_agent/adapters/persistence/firestore_search_settings.py`, `job_agent/application/preference_models.py`, `tests/domain/test_search_plan.py`, `tests/adapters/test_firestore_search_settings.py`
- Modify: `job_agent/domain/policies.py`, `job_agent/application/ports.py`, `job_agent/adapters/persistence/__init__.py`

**Interfaces:**
- Consumes: Task 2 `SearchPreferences`, `CollectorPlan`, normalisation.
- Produces in `job_agent.domain.policies`:

```python
@dataclass(frozen=True)
class SearchBudgets:
    max_roles_from_profile: int = 3
    max_queries: int = 8
    max_details_per_run: int = 20

@dataclass(frozen=True)
class SearchPlanBuild:
    plan: CollectorPlan | None   # None when there is no keyword at all
    total_queries: int           # before the max_queries cut

def profile_fingerprint(profile: Profile | None) -> str: ...  # sha256 hex of json.dumps(profile.model_dump(mode="json"), sort_keys=True); "" for None
def build_search_plan(preferences: SearchPreferences, profile: Profile | None, budgets: SearchBudgets, now: datetime) -> SearchPlanBuild: ...
```

  Keyword order: `keywords_include`, then (if `use_profile_keywords` and a profile exists) `profile.target_roles[:max_roles_from_profile]`, then `profile.search_keywords`; drop blanks, entries whose `normalize_keyword` matches any `keywords_exclude`, and normalised duplicates. Locations: preferences, else `profile.locations`, else `[None]`. Queries = keywords × locations in that order, cut to `max_queries`. Copy `posted_within_days`, `work_types`, `experience_levels`, exclusions, `max_details_per_run`, `preferences_version = preferences.version`, `profile_fingerprint`, `built_at = now`.
- Produces in `job_agent.application.preference_models`:

```python
DraftStatus = Literal["PENDING", "APPLIED", "CANCELLED", "EXPIRED"]

class PreferenceDraft(BaseModel):
    draft_id: str
    chat_id: str
    base_version: int
    preferences: SearchPreferences
    diff: list[str]
    status: DraftStatus = "PENDING"
    created_at: datetime
    expires_at: datetime

class DraftResolution(BaseModel):
    status: Literal["applied", "cancelled", "stale", "expired", "already_resolved", "not_found"]
    previous_status: DraftStatus | None = None
    preferences_version: int | None = None
    plan: CollectorPlan | None = None      # the plan written, when one was
    plan_kept: bool = False                # applied but no keywords: previous plan kept
```

- Produces port in `job_agent.application.ports`:

```python
class SearchSettingsStore(Protocol):
    def load_preferences(self) -> SearchPreferences | None: ...
    def load_plan(self) -> CollectorPlan | None: ...
    def seed(self, preferences: SearchPreferences, compile: Callable[[SearchPreferences], CollectorPlan | None], *, force: bool, now: datetime) -> bool: ...
    def save_plan(self, plan: CollectorPlan) -> None: ...
    def create_draft(self, draft: PreferenceDraft) -> None: ...
    def resolve_draft(self, draft_id: str, chat_id: str, action: Literal["apply", "cancel"], now: datetime,
                      compile: Callable[[SearchPreferences], CollectorPlan | None]) -> DraftResolution: ...
```

- Produces `FirestoreSearchSettingsStore(client)` on `settings/search_preferences`, `settings/search_plan` (same document the collector reads; write `plan.model_dump(mode="json")`) and `search_preference_drafts/{draft_id}`. `seed` and `resolve_draft` use `@firestore.transactional`, reading every document before writing. `seed`: if preferences exist and not `force`, return `False`; else write preferences with `version = current + 1` (1 when absent) and `updated_at = now`, the compiled plan if not `None`, return `True`. `resolve_draft`:
  - missing draft, or draft `chat_id` ≠ `chat_id` → `not_found`;
  - status ≠ PENDING → `already_resolved` with `previous_status`;
  - `action == "cancel"` → write CANCELLED, `cancelled`;
  - `now >= expires_at` → write EXPIRED, `expired`;
  - current version ≠ `base_version` → write EXPIRED, `stale`;
  - else new = draft preferences with `version = base_version + 1`, `updated_at = now`; plan = `compile(new)`; write preferences, the plan when not `None`, draft APPLIED; return `applied` with `plan` and `plan_kept = plan is None`.

- [ ] **Step 1: Write failing tests.** Domain:

```python
def test_plan_orders_keywords_and_cuts_queries(profile):
    prefs = SearchPreferences(keywords_include=["Django"], keywords_exclude=["frontend"], locations=["Remote", "España"], version=4)
    build = build_search_plan(prefs, profile, SearchBudgets(max_roles_from_profile=1, max_queries=3), NOW)
    assert [(q.keywords, q.location) for q in build.plan.search.queries] == [("Django", "Remote"), ("Django", "España"), (profile.target_roles[0], "Remote")]
    assert build.total_queries > 3 and build.plan.preferences_version == 4

def test_only_my_keywords_ignores_profile(profile):
    prefs = SearchPreferences(keywords_include=["Rust"], use_profile_keywords=False)
    assert {q.keywords for q in build_search_plan(prefs, profile, SearchBudgets(), NOW).plan.search.queries} == {"Rust"}

def test_no_keywords_builds_no_plan():
    assert build_search_plan(SearchPreferences(), None, SearchBudgets(), NOW).plan is None

def test_same_inputs_same_plan(profile):
    prefs = SearchPreferences(keywords_include=["Go"])
    assert build_search_plan(prefs, profile, SearchBudgets(), NOW) == build_search_plan(prefs, profile, SearchBudgets(), NOW)
```

  Also cover location fallback to the profile and to `None`, and exclusions copied into the plan. Adapter tests reuse the transactional fake from `tests/adapters/test_firestore_cv_tracking.py` (import `Client`): seed creates version 1 and the plan; seed without `force` on existing returns `False` and writes nothing; apply writes preferences (version + 1), plan and APPLIED; a second apply returns `already_resolved`; apply after another draft bumped the version returns `stale` and leaves preferences unchanged; expired returns `expired`; cancel writes only the draft; a draft of another chat returns `not_found`; `compile` returning `None` keeps the old plan document unchanged and sets `plan_kept`. Run two concurrent applies on two drafts with the same base (use `client.barrier`, adapting its path check) and assert exactly one `applied` and one `stale`.
- [ ] **Step 2: Run red:** `OPENROUTER_API_KEY=test .venv/bin/pytest -q tests/domain/test_search_plan.py tests/adapters/test_firestore_search_settings.py` — expect missing names.
- [ ] **Step 3: Implement** the domain functions, value objects, port and adapter as specified.
- [ ] **Step 4: Run green:** the two files plus `tests/domain tests/test_architecture.py`; expect zero failures.
- [ ] **Step 5: Commit:** `git add job_agent/domain/policies.py job_agent/application/ports.py job_agent/application/preference_models.py job_agent/adapters/persistence tests/domain/test_search_plan.py tests/adapters/test_firestore_search_settings.py` then `git commit -m "Compile search plans from stored preferences"`.

### Task 4: Migrate configuration and add the seed command

**Files:**
- Create: `job_agent/application/manage_search_preferences.py` (with `seed` only), `tests/application/test_manage_search_preferences.py`
- Modify: `job_agent/config.py`, `job_agent/application/build_profile.py`, `job_agent/webhook.py` (only the `preferred_locations` argument), `job_agent/entrypoints/cli.py`, `job_agent/application/__init__.py`
- Test: `tests/test_config.py`, `tests/test_bootstrap.py`, `tests/application/test_ensure_profile.py`, `tests/application/test_manage_search_preferences.py`, `tests/test_webhook_bootstrap.py`

**Interfaces:**
- Consumes: Task 3 `SearchBudgets`, `build_search_plan`, `SearchSettingsStore`, `FirestoreSearchSettingsStore`.
- Produces:
  - `SearchConfig` keeps only `max_roles_from_profile`, `max_queries`, `max_details_per_run`, `sources`; `Config.search_budgets() -> SearchBudgets`. `LEGACY_SEARCH_KEYS = ("locations", "extra_keywords", "posted_within_days", "work_types", "experience_levels", "exclude_companies", "exclude_title_keywords")`. `load_config` logs one warning naming the legacy keys found (never their values) and pointing to `seed-search-preferences`, and ignores them.
  - `legacy_search_preferences(path: Path) -> SearchPreferences` in `job_agent/config.py`: reads those keys from a YAML file (`extra_keywords` → `keywords_include`); raises `ValueError` if none is present.
  - `EnsureProfile(..., preferred_locations: Callable[[], list[str]] = lambda: [])`, called at inference time. The webhook passes `lambda: (settings.load_preferences() or SearchPreferences()).locations` with a `FirestoreSearchSettingsStore` built from its existing client.
  - `ManageSearchPreferences.__init__(self, *, store: SearchSettingsStore, profiles: ProfileStore, budgets: SearchBudgets, interpreter: PreferenceInterpreter | None = None, clock: Callable[[], datetime] = lambda: datetime.now(timezone.utc), new_id: Callable[[], str] = lambda: uuid4().hex)` and `seed(self, preferences: SearchPreferences, *, force: bool) -> bool`, compiling with the stored profile (`profiles.load()` → `StoredProfile | None`).
  - CLI `seed-search-preferences [--from-config PATH] [--force]` (default: the active `config.yaml`): requires `FIRESTORE_PROJECT_ID` and credentials (same messages as the collector), builds `FirestoreSearchSettingsStore` and `FirestoreProfileStore` on one client, calls `seed`, prints `Preferencias publicadas en Firestore` or `Ya existen preferencias; usa --force para reemplazarlas` (exit 0), `Error de configuración: …` (exit 2).

- [ ] **Step 1: Write failing tests.**

```python
def test_legacy_search_keys_warn_and_are_ignored(tmp_path, caplog):
    path = tmp_path / "config.yaml"
    path.write_text("search:\n  locations: [Remote]\n  exclude_companies: [Secreta]\n  max_queries: 4\n")
    cfg = load_config(path)
    assert cfg.search.max_queries == 4 and not hasattr(cfg.search, "locations")
    assert "seed-search-preferences" in caplog.text and "Secreta" not in caplog.text

def test_legacy_preferences_read_from_old_yaml(tmp_path):
    path = tmp_path / "old.yaml"
    path.write_text("search:\n  extra_keywords: [Django]\n  locations: [Remote]\n  posted_within_days: 2\n")
    assert legacy_search_preferences(path).keywords_include == ["Django"]

def test_profile_inference_reads_current_locations(...):  # provider returns ["España"] after a change; inferer receives ["España"]
def test_seed_compiles_plan_and_does_not_overwrite_without_force(...): ...
def test_cli_seed_reads_from_config_path(monkeypatch, tmp_path): ...  # monkeypatch the store factory; no real Firestore
```

- [ ] **Step 2: Run red:** `OPENROUTER_API_KEY=test .venv/bin/pytest -q tests/test_config.py tests/test_bootstrap.py tests/application` — expect failures for the new names.
- [ ] **Step 3: Implement** as specified. Do not edit `config.yaml` yet (Task 8 does, after the operator migrates).
- [ ] **Step 4: Run green:** `OPENROUTER_API_KEY=test .venv/bin/pytest -q` (all pass).
- [ ] **Step 5: Commit:** `git add job_agent tests` then `git commit -m "Move search preferences out of config and add seed command"`.

### Task 5: Interpret preference changes

**Files:**
- Create: `job_agent/domain/preference_edits.py`, `tests/domain/test_preference_edits.py`
- Modify: `job_agent/adapters/llm/prompts.py`, `job_agent/adapters/llm/tasks.py`, `job_agent/adapters/llm/__init__.py`, `job_agent/application/ports.py`
- Test: `tests/domain/test_preference_edits.py`, `tests/adapters/test_llm_adapters.py`

**Interfaces:**
- Consumes: Task 2 `SearchPreferences`.
- Produces in `job_agent.domain.preference_edits`:

```python
PreferenceField = Literal["keywords_include", "keywords_exclude", "use_profile_keywords", "locations", "posted_within_days",
                          "work_types", "experience_levels", "exclude_companies", "exclude_title_keywords", "none"]
LIST_FIELDS = {"keywords_include", "keywords_exclude", "locations", "work_types", "experience_levels", "exclude_companies", "exclude_title_keywords"}

class PreferenceOperation(BaseModel):
    action: Literal["add", "remove", "set", "unclear"]
    field: PreferenceField = Field(description="'none' only for unclear")
    values: list[str] = Field(description="Items to add/remove/set; for posted_within_days one number, for use_profile_keywords 'true' or 'false'")
    explanation: str = Field(description="Short Spanish note; required for unclear")

class PreferenceEdit(BaseModel):
    operations: list[PreferenceOperation]

@dataclass(frozen=True)
class EditOutcome:
    preferences: SearchPreferences | None   # None when nothing valid changed
    problems: list[str]                      # Spanish, safe to show

def apply_operations(current: SearchPreferences, edit: PreferenceEdit) -> EditOutcome: ...
def preference_diff(old: SearchPreferences, new: SearchPreferences) -> list[str]: ...
```

  `apply_operations` works on a copy: `add`/`remove` only on `LIST_FIELDS` (remove compares normalised), `set` on any field (lists replaced; scalar from `values[0]`), `unclear` adds its explanation to `problems`. Any invalid operation (wrong action/field pair, bad number or bool, `ValidationError` when rebuilding the model) adds a problem and **discards the whole edit** (`preferences=None`). If the result `content_equals` the current one, return `preferences=None` with the problem `"No encontré cambios para aplicar."`. `version` and `updated_at` are untouched. `preference_diff` returns lines in field order, e.g. `"➕ Palabras clave: Rust, Go"`, `"➖ Ubicaciones: Colombia"`, `"🗓 Días desde la publicación: 7"`, `"🔁 Usar keywords del perfil: no"`, with a Spanish label for every field.
- Produces port `PreferenceInterpreter.interpret(current: SearchPreferences, request: str) -> PreferenceEdit` and `LlmPreferenceInterpreter(model: StructuredModel)` calling `model.complete(system=prompts.PREFERENCES_SYSTEM, content=prompts.preferences_content(current, request), schema=PreferenceEdit, effort="low", max_tokens=4000)`. `PREFERENCES_SYSTEM` (English, like the other prompts) states: return only operations for what the candidate asked; never touch unmentioned fields; `exclude_companies` may only contain company names the candidate literally wrote; an excluded industry ("nada de bancos") becomes `add` on `exclude_title_keywords` with the literal word and its obvious English/Spanish form (e.g. `banco`, `bank`), never a list of guessed companies; map Spanish modality/seniority words to the allowed enum values listed verbatim; `posted_within_days` 1–30; "solo quiero…" sets `use_profile_keywords` to `false` and `keywords_include` to what was asked; anything else is `unclear` with a Spanish explanation.

- [ ] **Step 1: Write failing tests.**

```python
def test_add_and_remove_keep_other_fields():
    current = SearchPreferences(keywords_include=["Python"], locations=["Remote", "Colombia"], version=3)
    edit = PreferenceEdit(operations=[op("add", "keywords_include", ["Go"]), op("remove", "locations", ["colombia"])])
    out = apply_operations(current, edit)
    assert out.preferences.keywords_include == ["Python", "Go"] and out.preferences.locations == ["Remote"]
    assert out.preferences.version == 3 and out.problems == []

@pytest.mark.parametrize("bad", [op("add", "posted_within_days", ["3"]), op("set", "posted_within_days", ["40"]),
                                 op("set", "work_types", ["office"]), op("set", "use_profile_keywords", ["quizás"]), op("set", "none", [])])
def test_invalid_operation_discards_whole_edit(bad):
    out = apply_operations(SearchPreferences(), PreferenceEdit(operations=[op("add", "keywords_include", ["Go"]), bad]))
    assert out.preferences is None and out.problems

def test_unclear_only_and_no_change_create_nothing(): ...
def test_diff_lists_added_removed_and_scalars(): ...
def test_llm_interpreter_sends_current_preferences_and_text(): ...  # fake StructuredModel records system/content/schema
```

  Helper: `def op(action, field, values, explanation=""): return PreferenceOperation(action=action, field=field, values=values, explanation=explanation)`.
- [ ] **Step 2: Run red:** `OPENROUTER_API_KEY=test .venv/bin/pytest -q tests/domain/test_preference_edits.py tests/adapters/test_llm_adapters.py`.
- [ ] **Step 3: Implement** as specified.
- [ ] **Step 4: Run green:** the two files plus `tests/test_architecture.py`.
- [ ] **Step 5: Commit:** `git add job_agent/domain/preference_edits.py job_agent/adapters/llm job_agent/application/ports.py tests/domain/test_preference_edits.py tests/adapters/test_llm_adapters.py` then `git commit -m "Interpret natural-language search preference changes"`.

### Task 6: Manage preferences use case

**Files:**
- Modify: `job_agent/application/manage_search_preferences.py`, `job_agent/application/preference_models.py`, `job_agent/application/__init__.py`
- Test: `tests/application/test_manage_search_preferences.py`

**Interfaces:**
- Consumes: Tasks 3–5.
- Produces value objects in `preference_models`:

```python
class PreferencesView(BaseModel):
    preferences: SearchPreferences | None
    plan: CollectorPlan | None

class Proposal(BaseModel):
    kind: Literal["draft", "rejected", "missing_preferences"]
    draft: PreferenceDraft | None = None
    plan_preview: CollectorPlan | None = None
    total_queries: int = 0
    problems: list[str] = []

class RebuildResult(BaseModel):
    status: Literal["rebuilt", "no_keywords", "no_preferences"]
    plan: CollectorPlan | None = None
```

- Produces methods on `ManageSearchPreferences`:
  - `show() -> PreferencesView` — two store reads; never touches `interpreter`.
  - `propose(request: str, chat_id: str) -> Proposal` — missing preferences → `missing_preferences`; `interpreter.interpret`; `apply_operations`; `preferences is None` → `rejected` with problems; else create `PreferenceDraft(draft_id=new_id(), base_version=current.version, diff=preference_diff(...), created_at=now, expires_at=now + timedelta(hours=24))`, persist it, and return `draft` with `plan_preview`/`total_queries` from `build_search_plan` against the stored profile. The free text is never stored.
  - `resolve(draft_id: str, chat_id: str, action: Literal["apply", "cancel"]) -> DraftResolution` — reads the profile first, then `store.resolve_draft(..., compile=lambda p: build_search_plan(p, profile, budgets, now).plan)`.
  - `rebuild_plan() -> RebuildResult` — no preferences → `no_preferences`; plan `None` → `no_keywords` (old plan untouched); else `store.save_plan` → `rebuilt`.

- [ ] **Step 1: Write failing tests** with an in-memory store (add `MemorySearchSettings` to `tests/fakes.py`), a recording interpreter and a fixed clock:

```python
def test_show_never_calls_interpreter(service, interpreter):
    view = service.show()
    assert interpreter.calls == [] and view.preferences.version == 1

def test_propose_stores_draft_without_free_text(service, store):
    proposal = service.propose("quiero Go remoto", "42")
    assert proposal.kind == "draft" and store.drafts[proposal.draft.draft_id].base_version == 1
    assert "quiero Go remoto" not in repr(store.drafts)
    assert proposal.draft.expires_at - proposal.draft.created_at == timedelta(hours=24)

def test_apply_twice_applies_once(service): ...
def test_apply_without_keywords_keeps_previous_plan(service, store): ...
def test_rebuild_after_profile_change_uses_new_profile(service, store, profiles): ...
def test_propose_without_preferences_asks_for_migration(...): ...
```

- [ ] **Step 2: Run red:** `OPENROUTER_API_KEY=test .venv/bin/pytest -q tests/application/test_manage_search_preferences.py`.
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Run green:** that file plus `tests/test_architecture.py`.
- [ ] **Step 5: Commit:** `git add job_agent/application tests/application/test_manage_search_preferences.py tests/fakes.py` then `git commit -m "Coordinate search preference drafts and plan rebuilds"`.

### Task 7: Telegram `/preferencias` and buttons

**Files:**
- Create: `job_agent/adapters/notifications/telegram_preferences.py`, `tests/adapters/test_telegram_preferences.py`
- Modify: `job_agent/entrypoints/telegram.py`
- Test: `tests/adapters/test_telegram_preferences.py`, `tests/entrypoints/test_telegram_webhook.py`

**Interfaces:**
- Consumes: Task 6 `ManageSearchPreferences` and value objects.
- Produces `TelegramPreferencesChat(token: str, chat_id: str, client: httpx.Client | None = None)` with:
  - `show(view: PreferencesView) -> None` (`sendMessage`, HTML): every field with its Spanish label, `N búsquedas en el plan vigente` and, when `plan.preferences_version != preferences.version`, `⚠️ El plan aún no refleja la versión actual de tus preferencias`; missing preferences → the migration hint `No hay preferencias guardadas. Ejecuta job_agent seed-search-preferences en tu Mac.`,
  - `proposal(proposal: Proposal) -> None` — for `draft`: `Entendí estos cambios:`, diff lines, `<N> búsquedas`, first five queries `keywords @ location`, a cut warning when `total_queries > len(queries)`, and `reply_markup = {"inline_keyboard": [[{"text": "✅ Aplicar", "callback_data": f"pref:apply:{id}"}, {"text": "❌ Cancelar", "callback_data": f"pref:cancel:{id}"}]]}`; for `rejected`: `No apliqué cambios:` plus problems; for `missing_preferences`: the migration hint,
  - `resolved(message_id: int, resolution: DraftResolution) -> None` (`editMessageText`, keyboard removed): `✅ Aplicado: N búsquedas`, `✅ Preferencias guardadas. El plan se mantiene hasta que haya palabras clave.`, `❌ Cancelado`, `⌛ Esta propuesta ya no es válida; vuelve a escribir /preferencias con lo que quieres.` for stale/expired, and the previous outcome for `already_resolved`,
  - `answer(callback_id: str, text: str) -> None` (`answerCallbackQuery`).

  Every interpolated value goes through `html.escape`; messages are capped at `TELEGRAM_LIMIT`. HTTP or `ok` failures raise `RuntimeError` without the token or body.
- Entrypoint: `add_telegram_webhook(..., preferences: ManageSearchPreferences, preferences_chat: PreferencesChat)` where `PreferencesChat` is a Protocol with the four methods. Constants `PREFERENCES_COMMAND = "/preferencias"`, `PREFERENCES_REVIEWING = "Revisando tus preferencias…"`, `PREFERENCES_FAILED = "No pude revisar tus preferencias. Inténtalo de nuevo más tarde."`. `HELP` gains `"\n/preferencias — ver o cambiar el tipo de ofertas que busco"`.
  - Message `/preferencias` (validate private chat and `from.id` like `/ajustar_cv`): no text after the command → synchronous `preferences_chat.show(preferences.show())`; with text → send `PREFERENCES_REVIEWING`, then in a background task `preferences_chat.proposal(preferences.propose(text, chat_id))`; any exception → log the type name, send `PREFERENCES_FAILED`.
  - `callback_query` updates: dedupe by `update_id`; require `message.chat.id == chat_id`, `message.chat.type == "private"`, `from.id == chat_id`, `data` matching `^pref:(apply|cancel):[0-9a-f]{32}$`; otherwise answer `Acción no válida` (when an id exists) and do nothing. Valid: `resolve` synchronously, `answer(id, short status)`, then `resolved(message_id, resolution)`. A failure answers `No pude completar la acción` and logs only the type name.
  - `run_build`, after `build_profile.execute()` succeeds: `preferences.rebuild_plan()`; on exception send `Tu perfil se guardó, pero no pude actualizar el plan de búsqueda.`; on `no_keywords` send `Tu perfil se guardó, pero no hay palabras clave para buscar; agrega alguna con /preferencias.`

- [ ] **Step 1: Write failing tests.**

```python
def test_preferences_without_text_is_synchronous_and_deterministic(http, interpreter, chat):
    response = http.post("/webhooks/telegram", headers=SECRET, json=message("/preferencias"))
    assert response.status_code == 200 and interpreter.calls == [] and chat.calls == ["show"]

def test_preferences_with_text_acknowledges_then_proposes(http, events):
    http.post("/webhooks/telegram", headers=SECRET, json=message("/preferencias quiero Go"))
    assert events.index("ack") < events.index("interpret") < events.index("proposal")

@pytest.mark.parametrize("update", [callback(chat=7), callback(sender=7), callback(data="pref:apply:../x"), callback(data="other")])
def test_invalid_callbacks_change_nothing(http, store, update): ...

def test_apply_button_applies_once_and_edits_message(http, store, chat): ...  # two identical callbacks → one applied, then already_resolved
def test_build_profile_rebuilds_plan(http, store): ...
def test_proposal_escapes_html(chat_http): ...  # a diff containing "<b>&" is escaped; keyboard data is pref:apply:<id>
```

- [ ] **Step 2: Run red:** `OPENROUTER_API_KEY=test .venv/bin/pytest -q tests/adapters/test_telegram_preferences.py tests/entrypoints/test_telegram_webhook.py`.
- [ ] **Step 3: Implement.** Existing commands and their tests stay untouched apart from the new constructor arguments (fakes that fail on any use, as done for the CV ports).
- [ ] **Step 4: Run green:** both files plus `tests/test_architecture.py`.
- [ ] **Step 5: Commit:** `git add job_agent/adapters/notifications/telegram_preferences.py job_agent/entrypoints/telegram.py tests/adapters/test_telegram_preferences.py tests/entrypoints/test_telegram_webhook.py` then `git commit -m "Add Telegram search preferences command"`.

### Task 8: Wire the webhook, register updates and document the migration

**Files:**
- Modify: `job_agent/webhook.py`, `job_agent/config.py` (`LlmConfig.models` key `"preferences"`), `job_agent/adapters/notifications/telegram_profile.py`, `config.yaml`, `README.md`, `local_collector/README.md`
- Test: `tests/test_webhook_bootstrap.py`, `tests/adapters/test_telegram_profile.py`

**Interfaces:**
- Consumes: everything above.
- Produces: `build_webhook_app` builds one `FirestoreSearchSettingsStore(client)`, `ManageSearchPreferences(store=..., profiles=FirestoreProfileStore(client), budgets=cfg.search_budgets(), interpreter=LlmPreferenceInterpreter(build_llm(cfg.llm, "preferences")))` and `TelegramPreferencesChat(token, chat_id)`, passed to `add_telegram_webhook`. `register_webhook` sends `allowed_updates: ["message", "callback_query"]`; `BOT_COMMANDS` gains `{"command": "preferencias", "description": "Ver o cambiar el tipo de ofertas que busco"}`. `config.yaml` drops the migrated keys and says in a comment that they live in Firestore and are edited with `/preferencias`. README: migration order (`git show 38f1609:config.yaml > /tmp/config-legacy.yaml`, `.venv/bin/python -m job_agent seed-search-preferences --from-config /tmp/config-legacy.yaml`, deploy, `set-telegram-webhook` to re-register updates and menu), what `/preferencias` does with and without text, and that the collector drops excluded postings.

- [ ] **Step 1: Write failing tests:** the bootstrap drives `/preferencias` (no LLM; shows seeded preferences), `/preferencias quiero Go` with a fake LLM, a tap on Aplicar, and asserts `settings/search_plan` now has `Go` queries and `preferences_version == 2`; `register_webhook` posts `callback_query` in `allowed_updates` and `preferencias` in the menu.
- [ ] **Step 2: Run red:** `OPENROUTER_API_KEY=test .venv/bin/pytest -q tests/test_webhook_bootstrap.py tests/adapters/test_telegram_profile.py`.
- [ ] **Step 3: Implement wiring, registration, config cleanup and docs.**
- [ ] **Step 4: Final verification:** `OPENROUTER_API_KEY=test .venv/bin/pytest -q` (all pass); `.venv/bin/python -m compileall -q job_agent job_contracts local_collector tests`; `git diff --check` (no output); `.venv/bin/python -c "from job_agent.config import load_config; load_config()"` logs no legacy warning with the new `config.yaml`.
- [ ] **Step 5: Commit:** `git add job_agent/webhook.py job_agent/config.py job_agent/adapters/notifications/telegram_profile.py config.yaml README.md local_collector/README.md tests/test_webhook_bootstrap.py tests/adapters/test_telegram_profile.py` then `git commit -m "Wire Telegram search preferences and migrate config"`.
