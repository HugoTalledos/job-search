# Local LinkedIn Collector Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Run the existing LinkedIn MCP on the Mac three times daily and save new, complete postings to Cloud Firestore for a future remote agent.

**Architecture:** A new collection use case reads a search plan from Firestore, asks the LinkedIn source for IDs, skips known IDs before fetching details, then creates one Firestore document per new posting. The existing search and tailoring command remains available while the remote agent is built; the new collector has its own CLI command and scheduler entry.

**Tech Stack:** Python 3.10+, Pydantic, MCP Python SDK, `google-cloud-firestore`, Cloud Firestore, macOS `launchd`.

**Spec:** `docs/superpowers/specs/2026-10-02-local-linkedin-collector-design.md`

## Global Constraints

- Firestore paths: `settings/search_plan` and `job_postings/{job_key}`.
- Run LinkedIn locally through the current MCP session; no LLM, Telegram, CV, or S3 calls in the collector.
- Use Application Default Credentials outside the repository; no API ingestion service or SQL migration.
- Keep three daily runs and prevent overlapping local runs.
- Do not add version compatibility machinery or a persistent local queue.
- A known ID must not consume the detail budget; failed details must be retryable.

## Review Focus

- Missing or malformed `settings/search_plan`: fail before opening the LinkedIn MCP session (Task 2).
- Repeated LinkedIn IDs, including repeats in one search: one detail fetch and one document (Tasks 1 and 2).
- Firestore failure after some documents were saved: report failure; saved documents are skipped next run (Task 2).
- An oversized posting: report a specific save failure without truncating its description (Task 2).
- A missing or invalid service credential: fail visibly without starting a LinkedIn search (Task 3).

---

### Task 1: Batch admission before LinkedIn details

**Files:**
- Modify: `job_agent/adapters/job_sources/linkedin_mcp_job_source.py`
- Modify: `job_agent/domain/models.py`
- Test: `tests/adapters/test_linkedin_source.py`

**Interfaces:**
- Produce: `SourceCollection(jobs: list[JobPosting], leads: int, known: int, detail_errors: list[str])` in `domain/models.py` and `LinkedInMcpJobSource.collect_new(plan: SearchPlan, known_keys: Callable[[list[JobLead]], set[str]], max_details: int) -> SourceCollection`.
- Keep the existing `collect(plan, admit, max_details)` behavior for the current `run` command.

- [ ] **Step 1: Write failing adapter tests** for one batch lookup after `search_jobs`, preserving source order, deduplicating repeated IDs, and excluding known keys before the detail budget. Assert `get_job_details` is called only for admitted IDs.
- [ ] **Step 2: Run** `pytest tests/adapters/test_linkedin_source.py -q`; expect new tests to fail because `collect_new` does not exist.
- [ ] **Step 3: Implement** `collect_new(...)` by reusing `_search` and a shared detail-fetch helper. Call `known_keys` once with distinct `JobLead` values and compare against `lead_key(lead)`; retain per-detail failure isolation and return counts/errors in `SourceCollection`.
- [ ] **Step 4: Run** `pytest tests/adapters/test_linkedin_source.py -q`; expect pass, including existing `collect` tests.
- [ ] **Step 5: Commit** the adapter and its tests.

### Task 2: Firestore state and collector use case

**Files:**
- Create: `job_agent/application/collect_jobs.py`
- Create: `job_agent/adapters/persistence/firestore_store.py`
- Modify: `job_agent/application/ports.py`
- Modify: `job_agent/domain/models.py`
- Modify: `requirements.txt`
- Test: `tests/application/test_collect_jobs.py`
- Test: `tests/adapters/test_firestore_store.py`

**Interfaces:**
- Produce: `CollectorPlan(search: SearchPlan, max_details_per_run: int)` and `CollectionReport(leads: int, known: int, fetched: int, inserted: int, errors: list[str])` in `domain/models.py`.
- Produce: `JobCollectorSource.collect_new(plan: SearchPlan, known_keys: Callable[[list[JobLead]], set[str]], max_details: int) -> SourceCollection` and `CollectorStore` with `load_plan() -> CollectorPlan`, `known_keys(leads: list[JobLead]) -> set[str]`, and `save(job: JobPosting) -> bool` in `application/ports.py`.
- Produce: `CollectJobs(source: JobCollectorSource, store: CollectorStore).execute() -> CollectionReport` and `FirestoreCollectorStore(client: firestore.Client)` implementing the store port.

- [ ] **Step 1: Write failing use-case tests** for plan read before source access, known IDs not consuming the detail budget, repeated runs saving once, partial save failure reporting, and missing plan failure. Use in-memory store and source fakes.
- [ ] **Step 2: Run** `pytest tests/application/test_collect_jobs.py -q`; expect import or assertion failures.
- [ ] **Step 3: Implement** the models, ports, and `CollectJobs.execute()`. Let missing plan and Firestore read failures stop the run before MCP work; copy `SourceCollection` counts/errors into the report, add save failures, and give a failing CLI exit later.
- [ ] **Step 4: Write failing Firestore adapter tests** using a fake client or emulator for the exact document paths, `get_all` known-key lookup, create-only idempotence, and a visible oversized-document error. Do not make live Firebase credentials a test prerequisite.
- [ ] **Step 5: Implement** `FirestoreCollectorStore` with `google-cloud-firestore`: one document read for the plan, `get_all` on lead references, and `DocumentReference.create` for each full posting. Catch only the already-exists error as `False`; propagate other write errors. Add the dependency.
- [ ] **Step 6: Run** `pytest tests/application/test_collect_jobs.py tests/adapters/test_firestore_store.py tests/adapters/test_linkedin_source.py -q`; expect pass.
- [ ] **Step 7: Commit** the use case, adapter, dependency, and tests.

### Task 3: Bootstrap the search plan and expose the collector

**Files:**
- Modify: `job_agent/bootstrap.py`
- Modify: `job_agent/entrypoints/cli.py`
- Modify: `example.env`
- Test: `tests/test_bootstrap.py`
- Test: `tests/test_config.py`

**Interfaces:**
- Produce: `build_collector(cfg: Config) -> CollectJobs`, `python -m job_agent collect`, and `python -m job_agent seed-search-plan`. Add `FirestoreCollectorStore.save_plan(plan: CollectorPlan) -> None` for the one-time seed command.
- `seed-search-plan` reads the existing `data/profile.json`, calculates `SearchPreferences.plan_for(profile)` from `config.yaml`, and writes `settings/search_plan` with `max_details_per_run`.

- [ ] **Step 1: Write failing CLI/bootstrap tests** for `collect` without LLM, Telegram, or resume initialization; `seed-search-plan` from a stored profile; missing profile; missing plan; and invalid Firestore credentials. Assert nonzero exit for collection errors.
- [ ] **Step 2: Run** `pytest tests/test_bootstrap.py tests/test_config.py -q`; expect the new cases to fail.
- [ ] **Step 3: Implement** `build_collector`, `collect`, and `seed-search-plan`. Use `google.cloud.firestore.Client` with Application Default Credentials and an explicit `FIRESTORE_PROJECT_ID`; fail with a clear setup error if credentials or project ID are absent. Add credential instructions to `example.env` without adding secrets.
- [ ] **Step 4: Run** `pytest tests/test_bootstrap.py tests/test_config.py tests/application/test_collect_jobs.py -q`; expect pass.
- [ ] **Step 5: Commit** bootstrap, CLI, configuration example, and tests.

### Task 4: Schedule and document the local command

**Files:**
- Create: `scripts/macos/run_collector.sh`
- Modify: `scripts/macos/install_schedule.py`
- Modify: `README.md`
- Test: `tests/test_schedule.py`

**Interfaces:**
- Produce: `run_collector.sh` with the existing log/lock pattern and `install_schedule.py --component collector` for three daily collector runs. Preserve the existing scheduler mode until the remote agent is ready.

- [ ] **Step 1: Write failing scheduler tests** for collector program arguments, unchanged 08:00/14:00/22:00 intervals, and distinct collector/legacy labels so the two schedules cannot overwrite each other.
- [ ] **Step 2: Run** `pytest tests/test_schedule.py -q`; expect failures.
- [ ] **Step 3: Implement** `run_collector.sh` and `--component collector` in the installer. Document Firebase setup, one-time `seed-search-plan`, manual `collect`, scheduler installation, and the fact that the remote agent is a later stage.
- [ ] **Step 4: Run** `pytest tests/test_schedule.py tests/application/test_collect_jobs.py -q` and `git diff --check`; expect pass and no whitespace errors.
- [ ] **Step 5: Commit** the runner, installer, docs, and tests.

## Completion check

- [ ] Run the focused tests from Tasks 1–4 and inspect the final diff.
- [ ] Confirm `collect` has no path to profile inference, scoring, Telegram, or CV generation, and that the existing `run` command still works.
- [ ] Report that a real Firestore/LinkedIn run still requires project credentials, an initial plan document, and the remote-agent stage.
