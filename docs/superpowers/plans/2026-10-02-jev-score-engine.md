# Jev Score Engine Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Score pending Firestore job postings against a stored professional profile with Jev 1.13, then update each posting in place after an authenticated webhook starts the work in the background.

**Architecture:** A `job_agent/scoring` vertical slice owns its domain values, ports, Firestore adapter, Jev tool, and orchestration. The existing HTTP entrypoint schedules the use case inside the service and retains its empty 200 response. `local_collector` initializes new postings as pending.

**Tech Stack:** Python, Pydantic, FastAPI BackgroundTasks, Cloud Firestore, httpx, pytest.

**Spec:** `docs/superpowers/specs/2026-10-02-jev-score-engine-design.md`

## Global Constraints

- Profile: read and validate the direct `Profile` fields at `profiles/current`; this feature never creates or changes that document.
- Posting: `job_postings/{job_key}` keeps its existing payload. New postings receive `status: "PENDING"`; missing status also means pending. Unknown status is logged and skipped.
- Successful scoring updates that same document with `status: "EVALUATED"`, integer `score` 0–100, `confidence` 0–1, `score_model: "typesafe/jev-1.13"`, and UTC `evaluated_at`.
- One failed offer stays pending; other offers continue. Missing/invalid profile or collection-read failure aborts the run. Already evaluated offers are never rescored in this stage.
- Jev uses `POST https://openrouter.ai/api/alpha/decisions`, model `typesafe/jev-1.13`, one Score question per offer, and `OPENROUTER_API_KEY`. Normalize its 0–4 position to 0–100; no generated explanations.
- Webhook: `POST /webhooks/inference` with `X-API-Key` returns empty 200 after scheduling within the process, not after scoring. No separate score or run collection, durable queue, Telegram, or CV work.
- Local project rule: leave `docs/superpowers` files uncommitted and ignored; never force-add the spec or plan. Preserve existing unrelated `.gitignore` and `.agents/` changes.

## Review Focus

- Collector documents created before `status` existed must still be scored (Task 1).
- A malformed posting or unknown status must not prevent another valid pending offer from being scored (Tasks 1 and 3).
- An invalid Jev answer must never transition an offer to `EVALUATED` (Tasks 2 and 3).
- Repeated notifications must not call Jev again for an already evaluated offer (Tasks 1 and 4).
- A background exception must be logged without leaking keys or job descriptions, while the HTTP acknowledgement remains empty 200 (Task 4).

---

### Task 1: Firestore state and profile input

**Files:**
- Create: `job_agent/scoring/models.py`, `job_agent/scoring/ports.py`, `job_agent/scoring/firestore.py`, `job_agent/scoring/__init__.py`
- Modify: `local_collector/adapters/firestore_store.py`
- Test: `tests/scoring/test_firestore.py`, `tests/adapters/test_firestore_store.py`

**Interfaces:**
- Produce: `PendingPosting(document_id: str, job: JobPosting)` and `ScoreResult(score: int, confidence: float, model: str)` values; `ProfileReader.load() -> Profile`; `PendingPostingStore.list_pending() -> list[PendingPosting]` and `.mark_evaluated(document_id: str, result: ScoreResult) -> None`; `FirestoreScoringStore(client)` implements both reader and store ports.

- [ ] **Step 1: Write failing tests** for collector `create()` adding `status: "PENDING"`; profile read from `profiles/current` including missing/invalid document; pending listing with explicit/missing status and real snapshot IDs; skipping `EVALUATED`, unknown status and malformed job while retaining other valid jobs; stream failure propagation; and `mark_evaluated` updating only score fields on the original document.
- [ ] **Step 2: Run** `.venv/bin/python -m pytest tests/scoring/test_firestore.py tests/adapters/test_firestore_store.py -q`; expect failures for the missing slice and missing status.
- [ ] **Step 3: Implement** the named values and ports, the collector write, and `FirestoreScoringStore`. Use `Profile.model_validate`, `JobPosting.model_validate`, and Firestore `SERVER_TIMESTAMP`; log invalid document IDs without payload contents. Use `DocumentReference.update`, not `set`, to preserve collector fields.
- [ ] **Step 4: Run** both test files and `tests/test_architecture.py`; expect pass.
- [ ] **Step 5: Commit** only product code and tests for this task.

### Task 2: Independent Jev scoring tool

**Files:**
- Create: `job_agent/scoring/jev.py`
- Modify: `job_agent/scoring/ports.py`
- Test: `tests/scoring/test_jev.py`

**Interfaces:**
- Consumes: `ScoreResult` from Task 1.
- Produces: `ScoringTool.score(profile: Profile, job: JobPosting) -> ScoreResult` port and `JevScoringTool(api_key: str, client: httpx.Client | None = None)` adapter.

- [ ] **Step 1: Write failing MockTransport tests** for the exact Decisions URL, Authorization header, model, profile/job `state`, one `affinity` Score question with five concrete levels; response score `2.5` becoming `63` and confidence retained; malformed type/range/missing answer, HTTP error, and empty configured API key failing without producing a score.
- [ ] **Step 2: Run** `.venv/bin/python -m pytest tests/scoring/test_jev.py -q`; expect failure for the missing adapter.
- [ ] **Step 3: Implement** the tool using httpx and a typed response validator. Use the five level descriptions from the spec and half-up integer rounding of `score * 25`. Do not use the chat-completions adapter or expose the OpenRouter key in exceptions/logs.
- [ ] **Step 4: Run** Jev tests and architecture tests; expect pass.
- [ ] **Step 5: Commit** the tool, port, and tests.

### Task 3: Score pending offers use case

**Files:**
- Create: `job_agent/scoring/run.py`
- Test: `tests/scoring/test_run.py`

**Interfaces:**
- Consumes: `ProfileReader`, `PendingPostingStore`, `ScoringTool`, and `ScoreResult` from Tasks 1–2.
- Produces: `ScorePendingJobs(profile_reader, postings, scorer).execute() -> ScoreReport` with evaluated/failed counts; all offer-level failures are handled inside this use case.

- [ ] **Step 1: Write failing in-memory tests** for profile loaded before offers, no scorer calls with no pending offers, one result saved under the original document ID, continuing after one scorer or write failure, leaving failures pending for retry, and aborting before any score when profile or listing fails.
- [ ] **Step 2: Run** `.venv/bin/python -m pytest tests/scoring/test_run.py -q`; expect failure for the missing use case.
- [ ] **Step 3: Implement** `ScorePendingJobs` with a small `ScoreReport`, log offer IDs and exception classes only, and continue per offer. Let profile and listing errors propagate to the background wrapper.
- [ ] **Step 4: Run** the use-case tests and architecture tests; expect pass.
- [ ] **Step 5: Commit** the use case and tests.

### Task 4: Start work from the webhook

**Files:**
- Modify: `job_agent/entrypoints/http.py`, `job_agent/webhook.py`, `tests/entrypoints/test_http.py`, `tests/test_webhook_bootstrap.py`, `README.md`, `example.env`

**Interfaces:**
- Consumes: `ScorePendingJobs.execute() -> ScoreReport` from Task 3 and the Firestore/Jev adapters from Tasks 1–2.
- Produces: `create_app(runner: ScorePendingJobs, api_key: str) -> FastAPI` and `build_webhook_app() -> FastAPI`.

- [ ] **Step 1: Write failing HTTP and composition tests** for empty 200 after authorized scheduling, 401 without work, background execution using an injected runner, a background error logged without leaking payload/key, serialization by a per-instance lock, and startup requiring `OPENROUTER_API_KEY` while constructing only the scoring slice. Use an ASGI send spy with a blocked runner to prove the response body is sent before scoring finishes; TestClient alone waits for background tasks. Update the old synchronous-read tests to the new contract. Assert a second run sees no evaluated offers through the real Firestore adapter fake.
- [ ] **Step 2: Run** `.venv/bin/python -m pytest tests/entrypoints/test_http.py tests/test_webhook_bootstrap.py -q`; expect failures for the unconnected scoring flow.
- [ ] **Step 3: Implement** FastAPI BackgroundTasks and the lock in the HTTP entrypoint, plus scoring composition in `webhook.py`. Document `profiles/current` shape, the job fields, response semantics, `OPENROUTER_API_KEY`, retries by re-notifying, and the lack of automatic recovery after process interruption.
- [ ] **Step 4: Run** `OPENROUTER_API_KEY=test .venv/bin/python -m pytest -q` and `git diff --check`; expect pass. Inspect the diff for unrelated files and secrets.
- [ ] **Step 5: Commit** only product code, tests, and user documentation.
