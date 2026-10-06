# CV Review and Factual Corrections Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let the candidate review and correct the exact CV Markdown before PDF delivery, and retain confirmed factual corrections for this and future CVs.

**Architecture:** A private Firestore review record points to immutable Markdown revisions in Cloud Storage. Telegram proposes targeted text replacements, records confirmed candidate facts separately from inferred profile data, and approves only the active revision. Approval renders that Markdown to PDF and uses the existing delivery receipts; old PDFs remain untouched.

**Tech Stack:** Python 3.14, FastAPI, Pydantic, Firestore transactions, Firebase Cloud Storage, Telegram Bot API, pytest.

**Spec:** `docs/superpowers/specs/2026-10-05-cv-review-corrections-design.md`

## Global Constraints

- A factual correction confirmed by the candidate affects the current CV and future CVs; editorial changes affect only one CV review.
- Keep raw profile inference and the effective top-level fields of `profiles/current` separately; confirmed corrections take priority even after `/build-profile`.
- Never render or deliver a new PDF before approval of its exact Markdown revision; preserve previously delivered PDF artifacts.
- Use only the configured private Telegram chat and user; callback data contains short identifiers, never CV text or personal facts.
- Do not log CV contents, correction text, candidate profile, Telegram token, or job description.
- Use 16-character URL-safe random review/revision identifiers so combined Telegram callback data stays within 64 bytes.
- Run tests with `.venv/bin/python -m pytest`; the `.venv/bin/pytest` shebang points to a previous checkout.

## Review Focus

1. A stale approve callback must not publish an earlier revision; Task 4 tests revision compare-and-set.
2. A factual correction confirmed while the PDF is later cancelled must remain in the effective profile; Task 5 tests this independent lifetime.
3. A correction conflicting with `resume/base.md` must not reappear after `/build-profile`; Task 2 tests rebuild and Task 5 tests generated Markdown.
4. A Telegram delivery failure after approval must reuse the approved PDF and receipts without re-tailoring; Task 6 tests retry.
5. A correction reply to an unrelated message or from another chat must not change the draft; Task 7 tests ownership and message mapping.

## File map

- `job_agent/domain/cv_corrections.py`: typed fact operations, pure profile projection, deterministic checks for explicit denied claims.
- `job_agent/application/cv_review_models.py`: review, revision, proposal, and status values; keep user-facing workflow types out of the legacy tracking models.
- `job_agent/application/cv_review.py`: prepare, propose, confirm, approve, retry, and reopen a CV review; orchestrates ports, not Firestore/Telegram details.
- `job_agent/adapters/persistence/firestore_profile_corrections.py`: immutable correction history, active-version index, and transactional effective-profile projection.
- `job_agent/adapters/persistence/firestore_cv_reviews.py`: draft/revision state, ownership, message mapping, and revision compare-and-set.
- `job_agent/adapters/llm/cv_edit.py`: interpret a correction as exact old/new text replacements plus optional fact operations; never rewrite the entire Markdown silently.
- `job_agent/adapters/notifications/telegram_cv.py`: Markdown preview document, bounded summary, revision buttons, and PDF correction button.
- Existing profile, scoring, CV generation/tracking/artifact, webhook, prompt, and test files change only at the integration points named below.

---

### Task 1: Fact correction model and projection

**Files:** Create `job_agent/domain/cv_corrections.py`; test `tests/domain/test_cv_corrections.py`.

**Interfaces:** Produce `FactOperation(kind, subject, value=None)`, `CorrectionSet(version: int, operations: list[FactOperation])`, `apply_fact_operations(base: Profile, operations: list[FactOperation]) -> Profile`, and `contradictions(markdown: str, operations: list[FactOperation]) -> list[str]`. Supported kinds are `remove_language`, `set_language`, `remove_skill`, `set_skill_level`, `set_seniority`, `set_years_of_experience`, `deny_claim`, and `revoke` (whose subject is a prior correction ID). `apply_fact_operations` receives only the active non-revocation operations; the store resolves revocations. Unsupported/ambiguous input is rejected before persistence. A denied language/skill has a canonical subject and candidate-CV aliases, not a bare substring check.

- [ ] **Step 1: Write failing tests** for «no hablo inglés» removing English/B2 from `Profile.languages`, a skill-level replacement, ordered operations, a contradictory Markdown claim, and unrelated `B2B` text remaining valid. Assert the original `Profile` is unchanged.
- [ ] **Step 2: Verify red:** `.venv/bin/python -m pytest -q tests/domain/test_cv_corrections.py` fails because the new interface is absent.
- [ ] **Step 3: Implement** the typed operations and pure projection/checks; use normalized whole terms and validated field values, not arbitrary profile-path mutation.
- [ ] **Step 4: Verify green:** run the same test file and `.venv/bin/python -m pytest -q`.
- [ ] **Step 5: Commit** `job_agent/domain/cv_corrections.py` and its test as `Add typed candidate fact corrections`.

### Task 2: Persistent facts and effective profile

**Files:** Create `job_agent/adapters/persistence/firestore_profile_corrections.py`; modify `job_agent/adapters/persistence/firestore_profile.py`, `job_agent/application/build_profile.py`, `job_agent/application/build_professional_profile.py`, `job_agent/application/ports.py`, `job_agent/adapters/llm/tasks.py`, `job_agent/adapters/llm/prompts.py`, `job_agent/scoring/firestore.py`, `job_agent/application/manage_search_preferences.py`, `job_agent/webhook.py`; test `tests/adapters/test_firestore_profile_corrections.py`, `tests/application/test_ensure_profile.py`, `tests/adapters/test_llm_adapters.py`, `tests/test_webhook_bootstrap.py`.

**Interfaces:** Produce `ProfileCorrections.load() -> CorrectionSet`, `.confirm(operations: list[FactOperation], expected_version: int) -> CorrectionSet`, and `.apply_in_transaction(transaction, operations: list[FactOperation], expected_version: int) -> CorrectionSet`. The transaction variant lets Task 5 update a CV revision and the profile atomically. Confirmation appends immutable `profile_corrections/{id}` records, resolves revocations in the active index, advances `profile_corrections/current`, and projects from `profiles/current.inferred_profile` to the top-level effective fields. Extend `ProfileStore.save_inferred(stored: StoredProfile) -> StoredProfile` to reapply the active correction version before publishing; existing readers keep loading top-level effective fields. Extend `ProfileInferer.infer`, `JobMatcher.score`, and `ResumeTailor.tailor` with a keyword-only `corrections: CorrectionSet | None = None` argument and pass it to their prompts; Task 4 supplies the active set for new CV drafts, while existing callers remain valid. Rebuild the search plan after an effective-profile change with the existing preference-version guard.

- [ ] **Step 1: Write failing tests** for transaction conflict/idempotency, old profile-document migration, `confirm` updating the effective profile, revoking a correction by ID and restoring the inferred fact, `/build-profile` preserving a denied English claim despite a new inference, and scorer/CV/search-plan readers receiving the corrected profile. Include a base resume that says English B2 to prove correction precedence.
- [ ] **Step 2: Verify red:** run the new adapter test and named profile/bootstrap tests with `.venv/bin/python -m pytest -q`.
- [ ] **Step 3: Implement** the Firestore correction history and projection, then wire profile rebuild and the correction-aware prompts. Preserve the current metadata fields and expose a retryable notice if plan rebuilding fails after the fact commits.
- [ ] **Step 4: Verify green:** run the changed tests and the full suite.
- [ ] **Step 5: Commit** the correction store, profile integration, and tests as `Persist confirmed facts across profile rebuilds`.

### Task 3: Immutable CV review storage

**Files:** Create `job_agent/application/cv_review_models.py`, `job_agent/adapters/persistence/firestore_cv_reviews.py`; modify `job_agent/application/ports.py`, `job_agent/adapters/persistence/firebase_cv_artifacts.py`, `job_agent/application/cv_models.py`; test `tests/adapters/test_firestore_cv_reviews.py`, `tests/adapters/test_firebase_cv_artifacts.py`.

**Interfaces:** Produce `CvReviewStore.create_or_resume(posting_id: str, chat_id: str, key: CvVersionKey) -> CvReview`, `publish_revision(review_id: str, expected_revision_id: str | None, markdown_uri: str, proposal_id: str | None) -> CvRevision`, `publish_revision_in_transaction(transaction, review_id, expected_revision_id, markdown_uri, proposal_id) -> CvRevision`, `load(review_id: str, chat_id: str) -> CvReview`, and `approve(review_id: str, revision_id: str, chat_id: str) -> ApprovalResult`; `ApprovalResult` distinguishes approved, already approved, stale, and unknown. The transaction variant joins Task 2's correction transaction in Task 5. Add `CvArtifactStore.save_markdown(review_id, revision_id, markdown) -> str` and `.read_markdown(uri) -> str`. Add `corrections_version: int = 0` to `CvVersionKey` so denial-only corrections invalidate reuse; for version 0 preserve the existing hash encoding exactly, and otherwise append the correction version to the hash input.

- [ ] **Step 1: Write failing tests** for immutable Markdown upload, resume of an active draft, private-chat ownership, concurrent revision updates, stale/duplicate approval, correction-version changes to `version_id`, and legacy artifact reads.
- [ ] **Step 2: Verify red:** run the two new/changed adapter test files and `tests/application/test_generate_tailored_cv.py`.
- [ ] **Step 3: Implement** the review state and artifact ports. Upload immutable content before publishing its pointer; no published revision may refer to a failed upload.
- [ ] **Step 4: Verify green:** run the changed tests and full suite.
- [ ] **Step 5: Commit** review persistence and artifact changes as `Store immutable CV review revisions`.

### Task 4: Generate draft, then approve exact Markdown

**Files:** Create `job_agent/application/cv_review.py`; modify `job_agent/application/generate_tailored_cv.py`, `job_agent/application/ports.py`, `job_agent/adapters/persistence/firestore_cv_tracking.py`, `job_agent/webhook.py`; test `tests/application/test_cv_review.py`, `tests/application/test_generate_tailored_cv.py`.

**Interfaces:** Produce `CvReviewService.prepare(posting_id: str, chat_id: str, now: datetime) -> CvReview`, `.generate_draft(review_id: str, chat_id: str) -> CvRevision`, and `.approve(review_id: str, revision_id: str, chat_id: str) -> CvGenerationResult`. `generate_draft` invokes matcher/tailor once and saves Markdown; `approve` reads the active Markdown, checks confirmed contradictions, renders and saves PDF/readme, and hands the existing delivery workflow a READY version. Keep the original offer ID for «✅ Apliqué».

- [ ] **Step 1: Write failing tests** showing that draft generation calls no PDF renderer or `send_pdf`, approval renders the exact active Markdown, an old revision cannot be approved, a denial-only correction causes a new key, and a renderer failure leaves the review approved and retryable.
- [ ] **Step 2: Verify red:** `.venv/bin/python -m pytest -q tests/application/test_cv_review.py tests/application/test_generate_tailored_cv.py`.
- [ ] **Step 3: Implement** the separate lifecycle, retaining existing delivery receipts and legacy READY-version access. Include review ID/revision ID in immutable artifact identity so editorial revisions cannot collide.
- [ ] **Step 4: Verify green:** run the changed tests and full suite.
- [ ] **Step 5: Commit** lifecycle changes as `Require CV review before PDF rendering`.

### Task 5: Targeted correction proposals

**Files:** Create `job_agent/adapters/llm/cv_edit.py`; modify `job_agent/application/cv_review.py`, `job_agent/application/cv_review_models.py`, `job_agent/adapters/persistence/firestore_cv_reviews.py`, `job_agent/adapters/llm/prompts.py`, `job_agent/webhook.py`; test `tests/adapters/test_cv_edit.py`, `tests/application/test_cv_review.py`.

**Interfaces:** Produce `TextReplacement(old_text: str, new_text: str)` and `CvEditInterpreter.propose(markdown: str, instruction: str, profile: Profile, corrections: CorrectionSet) -> CvEditProposal(proposal_id: str | None, replacements: list[TextReplacement], fact_operations: list[FactOperation], explanation: str)`. `CvReviewStore.save_proposal(review_id, expected_revision_id, expected_corrections_version, proposal) -> str` persists a pending proposal, and `.resolve_proposal_in_transaction(transaction, review_id, proposal_id, action) -> ProposalResolution` is single-use. `CvReviewService.propose_edit(review_id, chat_id, instruction) -> CvEditProposal` returns the proposal with its stored ID; `.confirm_edit(review_id, proposal_id, chat_id) -> CvRevision` verifies each `old_text` occurs exactly once without overlap, applies only those replacements, uploads new Markdown, and confirms fact operations. Ambiguous edits yield a clarification, never a silent full rewrite.

- [ ] **Step 1: Write failing tests** for removal of a false English/B2 line, a style-only edit leaving the correction version unchanged, multiple exact replacements, missing/duplicate `old_text`, a whole-document replacement being rejected, rejected/stale/repeated proposals, and a confirmed fact remaining active after draft cancellation. Test that the proposed before/after text is the text eventually saved.
- [ ] **Step 2: Verify red:** run the new adapter test and `tests/application/test_cv_review.py`.
- [ ] **Step 3: Implement** targeted proposals and confirmation with revision compare-and-set. Where Firestore metadata and the factual projection must change together, perform one transaction after immutable Markdown upload; an orphan upload is acceptable, a half-confirmed profile/draft is not. Recompute the current offer's candidate-facing match summary after a factual correction before PDF delivery, without running the tailor again.
- [ ] **Step 4: Verify green:** run changed tests and full suite.
- [ ] **Step 5: Commit** interpreter and confirmation as `Apply reviewed CV edits and lasting facts`.

### Task 6: Telegram preview, buttons, and delivery retry

**Files:** Modify `job_agent/adapters/notifications/telegram_cv.py`, `job_agent/adapters/persistence/firestore_cv_tracking.py`, `job_agent/entrypoints/telegram.py`, `job_agent/application/cv_review.py`, `job_agent/webhook.py`; test `tests/adapters/test_telegram_cv.py`, `tests/adapters/test_firestore_cv_tracking.py`, `tests/entrypoints/test_telegram_webhook.py`, `tests/application/test_cv_review.py`, `tests/test_webhook_bootstrap.py`.

**Interfaces:** Add `TelegramCvDelivery.send_preview(chat_id: str, reply_to_message_id: int, review_id: str, revision_id: str, summary: str, markdown: str) -> PreviewReceipt(preview_message_id: int, markdown_message_id: int)` and `.send_edit_proposal(chat_id: str, reply_to_message_id: int, review_id: str, proposal_id: str, before: str, after: str, scope: Literal["global", "local"]) -> int`; long diffs are sent as a text document, with a bounded message summary. Use `cv:approve:<review_id>:<revision_id>`, `cv:edit:<review_id>`, `cv:cancel:<review_id>`, `cv:confirm:<review_id>:<proposal_id>`, `cv:reject:<review_id>:<proposal_id>`, and `cv:correct:<review_id>` callbacks within Telegram's 64-byte limit. Persist preview-message-to-review mapping; accept correction text only as a reply to a mapped preview in the configured private chat. PDF messages retain «✅ Apliqué» and add «✏️ Corregir CV». For PDFs delivered before these buttons existed, `/corregir_cv` as a reply resolves the ready version via `FirestoreCvTrackingStore.find_ready_by_pdf_message(message_id: int) -> ReadyCvVersion | None` and opens a draft from its stored Markdown.

- [ ] **Step 1: Write failing tests** for preview text and full Markdown document, valid approval/edit/cancel, stale buttons, reply ownership, the existing `/ajustar_cv` path, edit of a delivered PDF (including an older buttonless PDF via `/corregir_cv`), callback-size limit, and confirmed delivery retry reusing saved artifacts without matcher/tailor calls.
- [ ] **Step 2: Verify red:** run the adapter, entrypoint, and application tests named above.
- [ ] **Step 3: Implement** Telegram routing and receipts. Answer callbacks promptly, run slow work in background, and keep legacy offer/CV buttons functional.
- [ ] **Step 4: Verify green:** run changed tests and full suite.
- [ ] **Step 5: Commit** Telegram review flow as `Review and correct CVs in Telegram`.

### Task 7: End-to-end behavior and documentation

**Files:** Modify `README.md`, `job_agent/entrypoints/telegram.py`, `tests/test_webhook_bootstrap.py`; create `tests/entrypoints/test_cv_review_flow.py` only if the existing bootstrap test becomes unwieldy.

**Interfaces:** No new production interface. Exercise the assembled service from Telegram request through draft, factual correction, approval, PDF, and subsequent `/build-profile`/CV request.

- [ ] **Step 1: Write a failing integration test** for a candidate who denies English B2 in a draft: current Markdown and PDF omit it, `profiles/current` reflects the denial, a forced profile rebuild cannot restore it, and a later offer's CV receives the corrected profile. Add a separate exact-PDF-revision/retry case if Task 6 does not already cover it through the assembled service.
- [ ] **Step 2: Verify red:** `.venv/bin/python -m pytest -q tests/test_webhook_bootstrap.py tests/entrypoints/test_cv_review_flow.py` (omit the new path if unnecessary).
- [ ] **Step 3: Complete** composition, user-facing messages, and README examples for preview, correction, confirmation, approval, old PDF correction, and fact revocation. Keep the existing applied-proposal button.
- [ ] **Step 4: Verify green:** `.venv/bin/python -m pytest -q`, `git diff --check`, and review no sensitive values enter logs or callback data.
- [ ] **Step 5: Commit** integration and documentation as `Document and verify CV correction workflow`.
