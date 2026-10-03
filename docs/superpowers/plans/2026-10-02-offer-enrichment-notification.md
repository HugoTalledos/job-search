# Offer Enrichment and Notification Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Enrich pending offers with explicit language and salary details, score them, and notify via Telegram when the score reaches the configured threshold.

**Architecture:** Extend the existing `job_agent/scoring` vertical slice with a separate enrichment tool and an offer notification port. The Firestore adapter persists each stage on the original posting; the orchestration use case controls state transitions, while the webhook still only schedules background work. Jev enrichment and Jev scoring make separate typed Decisions requests.

**Tech Stack:** Python, Pydantic, FastAPI, Cloud Firestore, httpx, OpenRouter Decisions API, Telegram Bot API, pytest.

**Spec:** `docs/superpowers/specs/2026-10-02-offer-enrichment-notification-design.md`

## Global Constraints

- Process only `PENDING` postings; a missing status also counts as pending. Never enumerate `PENDING_NOTIFICATION` for notification in this webhook.
- Persist enrichment on the original `job_postings/{id}` before scoring: optional `required_language`, optional `salary_range`, and `enriched_at`. A saved `enriched_at` prevents repeating the enrichment on a later normal webhook call.
- Score below `matching.min_score_to_notify` becomes `EVALUATED`; score at or above it becomes `PENDING_NOTIFICATION` before one Telegram send attempt in that run. A confirmed send becomes `NOTIFIED` with `notified_at`.
- Enrichment or score failure leaves `PENDING`; Telegram failure leaves `PENDING_NOTIFICATION`. Continue processing other offers. A future normal webhook may process remaining `PENDING` offers; notification retry belongs to another mechanism.
- The score tool retains `ScoringTool.score(Profile, JobPosting) -> ScoreResult`. The enrichment tool is separate and uses the same Jev model in a separate Decisions request, selecting only literal candidates or `none`.
- Telegram messages include title, company, location, URL, and score; include language/salary only if found. Require `TELEGRAM_BOT_TOKEN` and `TELEGRAM_CHAT_ID` at startup. Never log the Bot API URL, token, or offer description.
- Keep `POST /webhooks/inference`, `X-API-Key`, empty 200 after scheduling, and the per-instance lock.
- Follow `.agents/rules/develop-standars.md`: DDD vertical slice and do not commit ignored specs/plans. Preserve unrelated `.gitignore` and `.agents/` changes.

## Review Focus

- A stored enrichment with both optional values absent still counts as completed and must not call Jev again (Task 1, Task 4).
- A Jev Choice containing an unknown candidate ID or a numeric-string confidence must not write enriched fields (Task 2).
- A salary mention without a numeric range or language marked merely preferred must not appear as a required field (Task 2).
- Telegram HTTP 200 with `ok: false` must leave the posting `PENDING_NOTIFICATION`, and its token must not enter logs (Task 3, Task 4).
- A subsequent webhook must skip existing `PENDING_NOTIFICATION` while still processing a `PENDING` offer that failed earlier (Task 1, Task 4).

---

### Task 1: Persist enrichment and recommendation states

**Files:**
- Modify: `job_agent/scoring/models.py`, `job_agent/scoring/ports.py`, `job_agent/scoring/firestore.py`
- Test: `tests/scoring/test_firestore.py`

**Interfaces:**
- Produces: `PostingEnrichment(required_language: str | None = None, salary_range: str | None = None)`; `PendingPosting(document_id: str, job: JobPosting, enrichment: PostingEnrichment | None = None)` where `None` means `enriched_at` absent; `PendingPostingStore.mark_enriched(document_id: str, enrichment: PostingEnrichment) -> None`, `.mark_scored(document_id: str, result: ScoreResult, notify: bool) -> None`, and `.mark_notified(document_id: str) -> None`.
- `FirestoreScoringStore` implements those operations using `DocumentReference.update` and Firestore server timestamps. Replace the old `.mark_evaluated` call sites in Task 4.

- [ ] **Step 1: Write failing Firestore tests** for `list_pending()` returning stored enrichment (including both values absent when `enriched_at` exists), ignoring `PENDING_NOTIFICATION` and `NOTIFIED`, and accepting old documents without `status`. Test `mark_enriched` writes only the two optional fields plus `enriched_at`, `mark_scored(..., notify=False/True)` writes the score fields and `EVALUATED`/`PENDING_NOTIFICATION`, and `mark_notified` writes only `NOTIFIED` and `notified_at`. Confirm unchanged `job` and collector metadata.
- [ ] **Step 2: Run** `.venv/bin/python -m pytest tests/scoring/test_firestore.py -q`; expect new tests to fail because enrichment and transitions do not exist.
- [ ] **Step 3: Implement** the named values, ports, and Firestore operations. Validate persisted optional values as strings or `None`; a malformed stored enrichment leaves the document untouched and logs its ID without payload. Use `firestore.SERVER_TIMESTAMP` for both new timestamps.
- [ ] **Step 4: Run** `.venv/bin/python -m pytest tests/scoring/test_firestore.py tests/test_architecture.py -q`; expect pass.
- [ ] **Step 5: Commit** only product code and tests for this task.

### Task 2: Independent Jev enrichment tool

**Files:**
- Create: `job_agent/scoring/enrichment.py`
- Modify: `job_agent/scoring/ports.py`
- Test: `tests/scoring/test_enrichment.py`

**Interfaces:**
- Consumes: `PostingEnrichment` from Task 1.
- Produces: `OfferEnricher.enrich(job: JobPosting) -> PostingEnrichment` port; `JevOfferEnricher(api_key: str, client: httpx.Client | None = None)` adapter.

- [ ] **Step 1: Write failing MockTransport tests** for literal language and numeric salary-range candidates from Spanish/English descriptions, including lines split by newline or sentence; no candidates returning empty enrichment without an HTTP request; Jev `choice` questions using stable IDs and `none`; chosen snippets copied exactly from the description; a preferred-only language or unrelated salary returning empty fields; invalid answer type, unknown choice, numeric-string confidence, and HTTP failure propagating without an enrichment result. Include an extraction response with both fields absent and confirm it is valid.
- [ ] **Step 2: Run** `.venv/bin/python -m pytest tests/scoring/test_enrichment.py -q`; expect failure for the missing tool.
- [ ] **Step 3: Implement** the tool. Candidate generation selects bounded literal spans containing common language names (`English`, `inglés`, `Spanish`, `español`, `Portuguese`, `portugués`, `French`, `francés`) or an explicit numeric salary range with currency marker (`$`, `USD`, `COP`, `EUR`, `€`). Keep at most eight candidates per field; criteria use IDs like `candidate_0` rather than raw text as keys. Ask Jev which candidate is required language and which is the offered salary range, each with `none`. Validate typed `choice`, ID membership, strict numeric confidence, and that the returned span occurs verbatim in `description`; omit a selection whose confidence is below 0.8. Send only the description in `state`, to `https://openrouter.ai/api/alpha/decisions` with `typesafe/jev-1.13`.
- [ ] **Step 4: Run** `.venv/bin/python -m pytest tests/scoring/test_enrichment.py tests/scoring/test_jev.py tests/test_architecture.py -q`; expect pass.
- [ ] **Step 5: Commit** the enrichment tool, port, and tests.

### Task 3: Telegram offer notifier

**Files:**
- Create: `job_agent/scoring/telegram.py`
- Modify: `job_agent/scoring/ports.py`
- Test: `tests/scoring/test_telegram.py`

**Interfaces:**
- Consumes: `PostingEnrichment` and `ScoreResult` from Task 1.
- Produces: `OfferNotifier.notify(job: JobPosting, result: ScoreResult, enrichment: PostingEnrichment) -> None` port; `TelegramOfferNotifier(token: str, chat_id: str, client: httpx.Client | None = None)` adapter.

- [ ] **Step 1: Write failing MockTransport tests** for required message fields, omission/presence of optional language and salary, escaping of HTML special characters in offer fields, safe handling of a very long title/URL under Telegram's 4096-character limit, invalid blank credentials, HTTP/network failure, and HTTP 200 with `ok: false` raising an error. Capture logs to ensure neither token nor description appears.
- [ ] **Step 2: Run** `.venv/bin/python -m pytest tests/scoring/test_telegram.py -q`; expect failure for the missing notifier.
- [ ] **Step 3: Implement** Telegram Bot API `sendMessage` with HTML escaping and `disable_web_page_preview`; bound individual field lengths before composing tags. Treat both non-2xx and `ok: false` as failure. Do not log response bodies or request URLs; the webhook composition in Task 4 sets the `httpx` logger to WARNING to prevent token-bearing INFO URLs.
- [ ] **Step 4: Run** `.venv/bin/python -m pytest tests/scoring/test_telegram.py tests/test_architecture.py -q`; expect pass.
- [ ] **Step 5: Commit** the notifier, port, and tests.

### Task 4: Orchestrate recommendation and wire the webhook

**Files:**
- Modify: `job_agent/scoring/run.py`, `job_agent/webhook.py`, `job_agent/config.py`, `README.md`, `example.env`
- Test: `tests/scoring/test_run.py`, `tests/test_webhook_bootstrap.py`, `tests/entrypoints/test_http.py`

**Interfaces:**
- Consumes: `PendingPostingStore`, `OfferEnricher`, `ScoringTool`, `OfferNotifier`, `PostingEnrichment`, and `ScoreResult` from Tasks 1–3.
- Produces: `ScorePendingJobs(profile_reader, postings, scorer, enricher, notifier, min_score_to_notify).execute() -> ScoreReport`; `ScoreReport` adds `notified` while retaining `evaluated` and `failed`. `build_webhook_app()` supplies real adapters and threshold from `load_config().matching.min_score_to_notify`.

- [ ] **Step 1: Write failing use-case and bootstrap tests** for the exact successful order enrich → persist enrichment → score → persist score → notify → persist `NOTIFIED`; inclusive threshold 70, score 69 `EVALUATED` without notification, saved empty enrichment skipping Jev, pre-existing `PENDING_NOTIFICATION` skipped, Telegram error leaving the stored score and `PENDING_NOTIFICATION`, enrichment/score/write failures leaving the correct state and continuing other offers, and a second webhook processing failed `PENDING` without notifying a prior `PENDING_NOTIFICATION`. Test missing Telegram credentials failing startup, valid configuration loading threshold, and the HTTP 200/401 contract. Assert the notifier never sees a result before `mark_scored` succeeds.
- [ ] **Step 2: Run** `.venv/bin/python -m pytest tests/scoring/test_run.py tests/test_webhook_bootstrap.py tests/entrypoints/test_http.py -q`; expect failures from the old scoring-only flow.
- [ ] **Step 3: Implement** the orchestration with one `try` boundary per offer and a separate counter for notifications. Reuse persisted enrichment when present, store score before send, and attempt Telegram once only for an offer scored in that invocation. Let profile/list errors propagate to the existing background wrapper. Wire Jev enrichment, Jev scoring, Telegram, and `load_config`; require both Telegram variables and validate threshold with `Field(ge=0, le=100)`. Set `logging.getLogger("httpx").setLevel(logging.WARNING)` in the web service composition. Update README and `example.env` with state semantics, optional fields, configuration, and the absence of notification retry.
- [ ] **Step 4: Run** `OPENROUTER_API_KEY=test .venv/bin/python -m pytest -q` with fake Telegram variables supplied by tests and `git diff --check`; expect pass. Inspect staged diff for secrets and unrelated `.gitignore`/`.agents/` changes.
- [ ] **Step 5: Commit** only product code, tests, and user documentation.
