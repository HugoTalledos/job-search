# Inference Webhook Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Accept an authenticated notification, read the collected Firestore offers, and respond with an empty HTTP 200 without running inference.

**Architecture:** A FastAPI entrypoint calls a small application use case through a read-only port. A Firestore adapter reads the collector's existing `job_postings` documents; a separate composition module builds only these parts for the HTTP service.

**Tech Stack:** Python, FastAPI, Uvicorn, Pydantic, `google-cloud-firestore`, pytest, httpx.

**Spec:** `docs/superpowers/specs/2026-10-02-inference-webhook-design.md`

## Global Constraints

- Endpoint: `POST /webhooks/inference`, no required body, `200 OK` with an empty body after a successful full read, including an empty collection.
- Authentication: `X-API-Key` must equal nonempty `JOB_AGENT_WEBHOOK_API_KEY`; absent or incorrect keys return `401` before any Firestore read.
- Source: read-only `job_postings` documents written by `local_collector`; validate each `job` with `job_contracts.JobPosting`.
- Firestore or schema failure: `500`, no posting or credential contents in the response. No inference, LLM, LinkedIn, Telegram, writes, or processed marker.
- Service configuration: `FIRESTORE_PROJECT_ID` and Google application credentials; fail at startup for a missing project or API key.

## Review Focus

- Empty collection still returns `200` and an empty body (Task 2).
- Malformed `job` fails the request rather than being silently skipped (Task 1).
- Incorrect or missing API key never calls the read use case (Task 2).
- `GET` on the webhook cannot trigger the read (Task 2).
- A Firestore stream that fails during iteration returns `500`, not a premature `200` (Tasks 1–2).

---

### Task 1: Read collected postings

**Files:**
- Modify: `job_agent/application/ports.py`
- Create: `job_agent/application/load_collected_jobs.py`
- Create: `job_agent/adapters/persistence/firestore_postings.py`
- Test: `tests/application/test_load_collected_jobs.py`, `tests/adapters/test_firestore_postings.py`

**Interfaces:**
- Produce: `CollectedPostingsRepository.list_postings() -> list[JobPosting]` protocol; `LoadCollectedJobs(repository).execute() -> list[JobPosting]`; `FirestorePostingsRepository(client).list_postings() -> list[JobPosting]`.

- [ ] **Step 1: Write failing tests** for the use case returning the repository's postings and an empty list, plus an adapter fake whose `collection("job_postings").stream()` yields collector-shaped documents. Assert full `job` validation, malformed data failure, and failure raised during stream iteration.
- [ ] **Step 2: Run** `.venv/bin/python -m pytest tests/application/test_load_collected_jobs.py tests/adapters/test_firestore_postings.py -q`; expect failure for missing interfaces.
- [ ] **Step 3: Implement** the three interfaces above. The adapter validates `snapshot.to_dict()["job"]` with `JobPosting.model_validate`; it does not write or skip invalid documents. Keep Firestore imports in the adapter.
- [ ] **Step 4: Run** the two test files and `tests/test_architecture.py`; expect pass.
- [ ] **Step 5: Commit** the port, use case, adapter, and tests.

### Task 2: HTTP contract and authentication

**Files:**
- Create: `job_agent/entrypoints/http.py`
- Test: `tests/entrypoints/test_http.py`
- Modify: `requirements.txt`

**Interfaces:**
- Consumes: `LoadCollectedJobs.execute() -> list[JobPosting]` from Task 1.
- Produces: `create_app(load_jobs: LoadCollectedJobs, api_key: str) -> FastAPI`.

- [ ] **Step 1: Write failing TestClient tests** for `POST /webhooks/inference` returning status `200` and `b""` for populated and empty repositories; absent/wrong `X-API-Key` returning `401` without a read; `GET` not reading; and a repository exception returning `500` with no sensitive data in the body. Assert `create_app` rejects a blank configured API key.
- [ ] **Step 2: Run** `.venv/bin/python -m pytest tests/entrypoints/test_http.py -q`; expect failure for the missing entrypoint.
- [ ] **Step 3: Add** FastAPI and Uvicorn to `requirements.txt`. Implement `create_app` with `hmac.compare_digest` for the header, an empty `Response(status_code=200)`, and a server log on read failure. Do not include the API key or posting data in responses or logs.
- [ ] **Step 4: Run** the HTTP tests; expect pass.
- [ ] **Step 5: Commit** the endpoint, dependency change, and tests.

### Task 3: Service composition and usage

**Files:**
- Create: `job_agent/webhook.py`
- Test: `tests/test_webhook_bootstrap.py`
- Modify: `example.env`, `README.md`

**Interfaces:**
- Consumes: `create_app` from Task 2 and `FirestorePostingsRepository` from Task 1.
- Produces: `build_webhook_app() -> FastAPI` and module-level `app` for `uvicorn job_agent.webhook:app`.

- [ ] **Step 1: Write failing tests** for missing `JOB_AGENT_WEBHOOK_API_KEY` and `FIRESTORE_PROJECT_ID`, a fake Firestore client created with the configured project, and successful app creation without calling `build_container`, LLM adapters, or `local_collector`. Check credential-construction errors are visible at startup.
- [ ] **Step 2: Run** `.venv/bin/python -m pytest tests/test_webhook_bootstrap.py -q`; expect failure for the missing composition module.
- [ ] **Step 3: Implement** `build_webhook_app()`, load local `.env`, and construct only `firestore.Client`, `FirestorePostingsRepository`, `LoadCollectedJobs`, and `create_app`. Expose `app`. Document `JOB_AGENT_WEBHOOK_API_KEY`, the startup command, a `curl` request with `X-API-Key`, the empty `200`, and the current no-inference behavior.
- [ ] **Step 4: Run** `.venv/bin/python -m pytest -q` and `git diff --check`; expect pass. Inspect the final diff for secret values and unintended changes.
- [ ] **Step 5: Commit** the composition, documentation, and tests.
