# Telegram CV on Demand Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Generate and deliver an evidence-based PDF CV when the candidate replies `/ajustar_cv` to a Telegram offer, with private artifacts and version history.

**Architecture:** The Telegram entrypoint resolves the replied-to offer and calls `GenerateTailoredCv.prepare` before acknowledging the request; the case use executes LLM, rendering, storage and delivery in the background. Firestore owns message correlation and version state; a private Cloud Storage bucket owns PDF, Markdown and README artifacts. Existing matcher, tailor and offer notification flows are reused.

**Tech Stack:** Python 3.10+, FastAPI/Starlette, Pydantic 2, Google Cloud Firestore and Storage, httpx, WeasyPrint, pytest.

**Spec:** `docs/superpowers/specs/2026-10-03-telegram-cv-on-demand-design.md`

## Global Constraints

- Trigger only on `/ajustar_cv` as a reply to an offer message in the configured private Telegram chat; require matching `chat.id` and `from.id`.
- Read `resume_path`, then `profiles/current`, then `job_postings/{id}`. The stored Jev score is not the detailed fit analysis.
- Preserve employers and dates; do not add unsupported skills or other invented facts. Show actual gaps in the chat summary and README.
- PDF generation is mandatory. Keep the Cloud Storage bucket private and store `gs://` paths, never a permanent public CV URL.
- `CV_READY` does not mean `APPLIED`. Keep every source-changing version; reuse a ready version for identical inputs.
- Do not change the local `job_agent run` workflow or require real Telegram, LLM or Firebase services in tests.

## File Structure

| File | Responsibility |
|---|---|
| `job_agent/scoring/models.py`, `ports.py`, `run.py`, `telegram.py` | Return Telegram receipts and persist offer-message correlation before `NOTIFIED`. |
| `job_agent/adapters/persistence/firestore_offer_messages.py` | Resolve reply message IDs and the unique legacy offer URL. |
| `job_agent/domain/models.py`, `adapters/llm/prompts.py` | Explicit required skills with priority, evidence and gaps in `JobMatch`. |
| `job_agent/adapters/resume/pdf_renderer.py` | Required Markdown-to-PDF conversion as bytes. |
| `job_agent/application/cv_models.py`, `generate_tailored_cv.py`, `ports.py` | Request/version values, case use, README content and adapter interfaces. |
| `job_agent/adapters/persistence/firestore_cv_tracking.py` | Atomic version claim, lease, state and history in Firestore. |
| `job_agent/adapters/persistence/firebase_cv_artifacts.py` | Private PDF, Markdown and README upload/download. |
| `job_agent/adapters/notifications/telegram_cv.py` | Summary and PDF delivery with Telegram receipts. |
| `job_agent/entrypoints/telegram.py`, `job_agent/webhook.py` | Command handling and production wiring. |
| `job_agent/config.py`, `example.env`, `README.md`, `requirements.txt` | Bucket configuration and operator setup. |

## Review Focus

1. A legacy replied-to message with an absent or ambiguous URL must not select a posting; pin this in Task 1.
2. Untrusted Markdown/HTML or image URLs must not make the PDF renderer fetch external resources; pin this in Task 2.
3. Two simultaneous requests for the same version, including a stale lease, must leave one active claim; pin this in Task 3.
4. A failed PDF render or partial Storage upload must not mark `CV_READY`; pin this in Tasks 2 and 4.
5. A failed Telegram document send after artifacts are ready must preserve the version and retry delivery without another LLM call; pin this in Task 4.

---

### Task 1: Correlate Telegram offer messages with Firestore postings

**Files:**
- Modify: `job_agent/scoring/models.py`, `job_agent/scoring/ports.py`, `job_agent/scoring/run.py`, `job_agent/scoring/telegram.py`
- Create: `job_agent/adapters/persistence/firestore_offer_messages.py`
- Modify: `job_agent/webhook.py`
- Test: `tests/scoring/test_run.py`, `tests/scoring/test_telegram.py`, `tests/adapters/test_firestore_offer_messages.py`, `tests/test_webhook_bootstrap.py`

**Interfaces:**
- Produce: `TelegramMessageRef(chat_id: str, message_id: int)` in `scoring.models`; `OfferNotifier.notify(job, result, enrichment) -> TelegramMessageRef`.
- Produce: `OfferMessageIndex.record(ref: TelegramMessageRef, posting_id: str) -> None`, `resolve(chat_id: str, message_id: int) -> str | None`, `resolve_unique_url(url: str) -> str | None`.
- Consume later: Task 5 calls the index to resolve the original message. Task 4 loads the posting by resolved ID.

- [ ] **Step 1: Write failing tests.** Pin the send/record/state ordering, including resend, and strict legacy lookup:

```python
def test_records_message_before_notified(runner, events):
    runner.execute()
    assert events == ["send:offer", "record:42:91:offer", "notified:offer"]

def test_ambiguous_legacy_url_is_rejected(index):
    assert index.resolve_unique_url("https://jobs.example/one") is None
```

Also assert `notify` returns `TelegramMessageRef("42", 91)`, two receipts can point to one posting, and a failed `record` leaves `PENDING_NOTIFICATION`.
- [ ] **Step 2: Run red tests:** `OPENROUTER_API_KEY=test .venv/bin/pytest -q tests/scoring/test_run.py tests/scoring/test_telegram.py tests/adapters/test_firestore_offer_messages.py`. Expect failures for the absent receipt/index API.
- [ ] **Step 3: Implement the interfaces.** Parse `result.message_id` only after Telegram returns `ok: true`; preserve error handling for malformed responses. Create `telegram_offer_messages/{chat_id}_{message_id}` after a successful send, including `posting_id` and server timestamp. Wire one Firestore-backed index into both scoring runners. `resolve_unique_url` queries `job_postings` by `job.url` and rejects ambiguous results.
- [ ] **Step 4: Run green tests:** the three test files above and `tests/test_webhook_bootstrap.py`; expect zero failures.
- [ ] **Step 5: Commit:** `git add job_agent/scoring job_agent/adapters/persistence/firestore_offer_messages.py job_agent/webhook.py tests/scoring tests/adapters/test_firestore_offer_messages.py tests/test_webhook_bootstrap.py` then `git commit -m "Track Telegram offer messages for CV replies"`.

### Task 2: Expose requirement evidence and require a PDF

**Files:**
- Modify: `job_agent/domain/models.py`, `job_agent/adapters/llm/prompts.py`
- Create: `job_agent/adapters/resume/pdf_renderer.py`
- Test: `tests/adapters/test_llm_adapters.py`, `tests/adapters/test_pdf_renderer.py`, `tests/domain/test_models.py`

**Interfaces:**
- Produce: `JobRequirement(name: str, priority: Literal["must", "nice"], covered: bool, evidence: str)` and `JobMatch.requirements: list[JobRequirement] = Field(default_factory=list)`; retain the existing `JobMatch` fields and local callers.
- Produce: `RequiredPdfRenderer.render(markdown_text: str) -> bytes`, raising `PdfRenderError` for a failed or empty PDF. Raw HTML is escaped and external resource fetching is denied.
- Consume later: Task 4 reads `match.requirements`, `match.reasons`, `match.gaps` and PDF bytes.

- [ ] **Step 1: Write failing tests.** Pin structured requirement evidence, backwards compatibility and required PDF behavior:

```python
def test_match_preserves_requirement_evidence(match):
    assert [(r.name, r.priority, r.covered) for r in match.requirements] == [
        ("Python", "must", True), ("Kubernetes", "nice", False)]

def test_pdf_render_rejects_empty_output(monkeypatch):
    monkeypatch.setattr("weasyprint.HTML.write_pdf", lambda self: b"")
    with pytest.raises(PdfRenderError):
        RequiredPdfRenderer().render("# Candidate")
```

Also assert old `JobMatch` JSON validates, valid bytes start `%PDF`, WeasyPrint exceptions raise `PdfRenderError`, and raw HTML/Markdown image URLs never trigger an external fetch.
- [ ] **Step 2: Run red tests:** `OPENROUTER_API_KEY=test .venv/bin/pytest -q tests/adapters/test_llm_adapters.py tests/adapters/test_pdf_renderer.py tests/domain/test_models.py`. Expect missing type/renderer failures.
- [ ] **Step 3: Implement the fields, prompt and `RequiredPdfRenderer.render`.** Ask the matcher to list each explicit skill/requisite, classify priority and mark evidence or gap. Escape raw HTML before Markdown conversion and pass a WeasyPrint `url_fetcher` that rejects every external fetch; validate nonempty PDF bytes. Keep the optional fallback in the legacy `write_outputs` function untouched.
- [ ] **Step 4: Run green tests:** the three files above; expect zero failures.
- [ ] **Step 5: Commit:** `git add job_agent/domain/models.py job_agent/adapters/llm/prompts.py job_agent/adapters/resume/pdf_renderer.py tests/adapters/test_llm_adapters.py tests/adapters/test_pdf_renderer.py tests/domain/test_models.py` then `git commit -m "Capture job requirements and require CV PDFs"`.

### Task 3: Persist version claims and private artifacts

**Files:**
- Create: `job_agent/application/cv_models.py`, `job_agent/adapters/persistence/firestore_cv_tracking.py`, `job_agent/adapters/persistence/firebase_cv_artifacts.py`
- Modify: `job_agent/application/ports.py`, `job_agent/config.py`, `requirements.txt`, `example.env`
- Test: `tests/adapters/test_firestore_cv_tracking.py`, `tests/adapters/test_firebase_cv_artifacts.py`, `tests/test_config.py`

**Interfaces:**
- Produce: `CvVersionKey(posting_id: str, resume_fingerprint: str, profile_fingerprint: str, job_fingerprint: str)` with deterministic `version_id` from SHA-256 of those values; `CvArtifacts(pdf_uri: str, markdown_uri: str, readme_uri: str)`. `PreparedCvRequest` carries the key, claim action, resume text, profile and posting; `CvGenerationResult` carries the key and generation/delivery statuses.
- Produce: `CandidateProfileReader.load() -> Profile` and `JobPostingReader.load(posting_id: str) -> JobPosting` in `application.ports`; `FirestoreCvTrackingStore` implements the latter from `job_postings/{posting_id}`.
- Produce: `CvTrackingStore.claim(key: CvVersionKey, now: datetime) -> ClaimResult` where `ClaimResult.action` is `"generate" | "reuse" | "in_progress"`; `mark_ready(key, artifacts, match, tailored)`, `mark_failed(key)`, `mark_summary_sent(key, message_id)`, `mark_pdf_sent(key, message_id)`, `mark_delivery_failed(key)` and `load_ready(key) -> ReadyCvVersion`. `ReadyCvVersion` includes artifacts and any delivery message IDs. `claim` uses a 30-minute `PROCESSING` lease and a Firestore transaction.
- Produce: `CvArtifactStore.save(key, pdf: bytes, markdown: str, readme: str) -> CvArtifacts` and `read_pdf(artifacts: CvArtifacts) -> bytes`. Paths are `cvs/{posting_id}/{version_id}/{cv.pdf,resume.md,README.md}` in `FIREBASE_STORAGE_BUCKET`.
- Consume later: Task 4 uses these ports; Task 5 supplies the configured bucket. The parent `application_tracking/{posting_id}` records `CV_READY`, first request, latest version and posting reference; each `versions/{version_id}` holds inputs, fit analysis, artifact URIs, timestamps and generation/delivery statuses.

- [ ] **Step 1: Write failing tests.** Pin key identity, atomic claims, tracking transitions and object paths:

```python
def test_claim_is_atomic_across_workers(store, key, now):
    with ThreadPoolExecutor(max_workers=2) as pool:
        actions = list(pool.map(lambda _: store.claim(key, now).action, range(2)))
    assert sorted(actions) == ["generate", "in_progress"]

def test_artifact_paths_are_private(artifacts, key):
    assert artifacts.pdf_uri == f"gs://private-bucket/cvs/offer/{key.version_id}/cv.pdf"
```

Also assert fingerprint changes create a new key; `READY` reuses, stale leases and `FAILED` reclaim, the first-request timestamp survives later versions, `CV_READY` appears only after `mark_ready`, posting loads by exact ID, three MIME types are correct, partial upload raises without returning complete artifacts, and empty bucket is rejected.
- [ ] **Step 2: Run red tests:** `OPENROUTER_API_KEY=test .venv/bin/pytest -q tests/adapters/test_firestore_cv_tracking.py tests/adapters/test_firebase_cv_artifacts.py tests/test_config.py`. Expect missing model/store/config failures.
- [ ] **Step 3: Implement models, ports and adapters.** Use Firestore transactions for `claim`; a claimed retry may overwrite only its deterministic, not-yet-ready object paths. Use `google-cloud-storage` with Application Default Credentials and no public ACL or permanent download token. Preserve generation state if an upload fails. Add the dependency and `FIREBASE_STORAGE_BUCKET` example.
- [ ] **Step 4: Run green tests:** the three files above; expect zero failures.
- [ ] **Step 5: Commit:** `git add job_agent/application/cv_models.py job_agent/application/ports.py job_agent/adapters/persistence/firestore_cv_tracking.py job_agent/adapters/persistence/firebase_cv_artifacts.py job_agent/config.py requirements.txt example.env tests/adapters/test_firestore_cv_tracking.py tests/adapters/test_firebase_cv_artifacts.py tests/test_config.py` then `git commit -m "Persist CV versions and private artifacts"`.

### Task 4: Coordinate CV generation and delivery

**Files:**
- Create: `job_agent/application/generate_tailored_cv.py`, `job_agent/adapters/notifications/telegram_cv.py`
- Modify: `job_agent/application/__init__.py`, `job_agent/application/ports.py`
- Test: `tests/application/test_generate_tailored_cv.py`, `tests/adapters/test_telegram_cv.py`

**Interfaces:**
- Produce: `GenerateTailoredCv.prepare(posting_id: str, now: datetime) -> PreparedCvRequest` and `execute(prepared: PreparedCvRequest, chat_id: str, reply_to_message_id: int) -> CvGenerationResult`. `prepare` reads `ResumeSource.read()`, `CandidateProfileReader.load()`, then `JobPostingReader.load(posting_id)` and claims the deterministic version. `execute` generates for `generate`, reads artifacts for `reuse`, and sends nothing for `in_progress`.
- Produce: `CvDelivery.send_summary(chat_id: str, reply_to_message_id: int, summary: str) -> int` and `send_pdf(chat_id: str, reply_to_message_id: int, pdf: bytes) -> int`; `TelegramCvDelivery` implements them with `sendMessage` and `sendDocument`. The case use saves each receipt before the next send.
- Consume: Task 1's posting lookup, Task 2's matcher/tailor/renderer and Task 3's tracking/artifact ports. Task 5 calls `prepare` before its acknowledgment and schedules `execute`.

- [ ] **Step 1: Write failing tests.** Pin preparation order, artifact completion and delivery retry:

```python
def test_prepare_reads_sources_in_order(use_case, events):
    use_case.prepare("offer", NOW)
    assert events[:4] == ["resume", "profile", "posting:offer", "claim"]

def test_delivery_failure_reuses_ready_artifacts(use_case, events):
    use_case.execute(READY_REQUEST, "42", 91)
    assert "match" not in events and "tailor" not in events
    assert events[-2:] == ["read_pdf", "send_pdf_failed"]
```

Also assert no LLM in `prepare`; missing CV/profile/posting or an empty description fails without rendering; the matcher/tailor receive the full inputs; README includes evidence/reasons/gaps/changes/fingerprints; upload precedes `mark_ready`; unsupported skills stay gaps; successful delivery stores both message IDs; a failed PDF send after a confirmed summary retries only the PDF; and LLM/PDF/partial Storage failures mark generation `FAILED` without `CV_READY`.
- [ ] **Step 2: Run red tests:** `OPENROUTER_API_KEY=test .venv/bin/pytest -q tests/application/test_generate_tailored_cv.py tests/adapters/test_telegram_cv.py`. Expect missing case use/delivery failures.
- [ ] **Step 3: Implement the case use and adapter.** Build the README with a pure `build_cv_readme(job, match, tailored, key) -> str` function in the use-case module. Keep factual rules in the existing tailor prompt; use the structured requirement evidence for the report. Keep `generation_status` and `delivery_status` independent. Parse Telegram response `result.message_id` for each send and save the summary receipt before attempting the PDF. A retry after partial delivery sends only the missing part; a fresh explicit request for a fully delivered ready version sends both again. Cap text to Telegram limits without cutting HTML entities or tags.
- [ ] **Step 4: Run green tests:** the two files above; expect zero failures.
- [ ] **Step 5: Commit:** `git add job_agent/application/generate_tailored_cv.py job_agent/application/__init__.py job_agent/application/ports.py job_agent/adapters/notifications/telegram_cv.py tests/application/test_generate_tailored_cv.py tests/adapters/test_telegram_cv.py` then `git commit -m "Generate and deliver tailored CVs"`.

### Task 5: Connect `/ajustar_cv` to the webhook and document operation

**Files:**
- Modify: `job_agent/entrypoints/telegram.py`, `job_agent/webhook.py`, `job_agent/adapters/notifications/telegram_profile.py`, `README.md`
- Test: `tests/entrypoints/test_telegram_webhook.py`, `tests/test_webhook_bootstrap.py`, `tests/adapters/test_telegram_profile.py`

**Interfaces:**
- Consume: Tasks 1–4. `add_telegram_webhook(..., cv_generator: GenerateTailoredCv, offer_messages: OfferMessageIndex)` handles the reply and schedules `execute` as a Starlette background task. `build_webhook_app` constructs Firestore, Storage, LLM matcher/tailor and PDF adapters.
- Produce: Telegram menu command `ajustar_cv`; immediate accepted reply exactly `Estoy ajustando tu CV para esta propuesta. Te enviaré el PDF al terminar.`

- [ ] **Step 1: Write failing tests.** Pin the command boundary and full webhook path:

```python
def test_valid_reply_acknowledges_before_generation(http, events):
    response = http.post("/webhooks/telegram", headers=SECRET, json=REPLY_COMMAND)
    assert response.status_code == 200
    assert events.index("ack") < events.index("match") < events.index("send_pdf")

def test_group_reply_does_not_generate(http, events):
    http.post("/webhooks/telegram", headers=SECRET, json=GROUP_REPLY)
    assert "match" not in events
```

Also assert a unique legacy URL works; no reply, wrong sender, non-bot message, ambiguous URL and wrong secret do no work; duplicate updates/claims start one generation; a ready version resends without LLM; `setMyCommands` includes `ajustar_cv`.
- [ ] **Step 2: Run red tests:** `OPENROUTER_API_KEY=test .venv/bin/pytest -q tests/entrypoints/test_telegram_webhook.py tests/test_webhook_bootstrap.py tests/adapters/test_telegram_profile.py`. Expect missing command/wiring failures.
- [ ] **Step 3: Implement entrypoint and wiring.** Validate the private chat and sender before lookup. Extract only `text_link` URLs from `reply_to_message.entities` for legacy lookup, and require the replied-to message to be from the bot. Use Task 1's index, then `prepare`; respond with a clear error for unresolved replies, `in_progress` for a live claim, and the exact acknowledgment for accepted work. Schedule `execute` only for generate/reuse. Notify the user of background failures without logging resume/job contents. Require `FIREBASE_STORAGE_BUCKET` when this service starts. Document bucket/IAM/Blaze setup, PDF dependency and menu re-registration in README.
- [ ] **Step 4: Run green tests and final verification:** `OPENROUTER_API_KEY=test .venv/bin/pytest -q` (all pass); `python -m compileall -q job_agent tests`; `git diff --check` (no output). If Firebase emulator access is available, verify Firestore claim and Storage upload against emulators; keep fake-adapter tests as the required gate.
- [ ] **Step 5: Commit:** `git add job_agent/entrypoints/telegram.py job_agent/webhook.py job_agent/adapters/notifications/telegram_profile.py README.md tests/entrypoints/test_telegram_webhook.py tests/test_webhook_bootstrap.py tests/adapters/test_telegram_profile.py` then `git commit -m "Add Telegram on-demand CV command"`.
