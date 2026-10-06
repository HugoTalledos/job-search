"""Prepare deterministic CV requests, then generate artifacts and deliver receipts separately."""

from __future__ import annotations

import hashlib
import json
from datetime import datetime

from ..domain.models import JobMatch, JobPosting, TailoredResume
from .cv_models import CvGenerationResult, CvVersionKey, PreparedCvRequest
from .ports import (
    CandidateProfileReader, CvArtifactStore, CvDelivery, CvTrackingStore, JobMatcher,
    JobPostingReader, PdfRenderer, ResumeSource, ResumeTailor,
)


def _fingerprint(value: object) -> str:
    canonical = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'))
    return hashlib.sha256(canonical.encode('utf-8')).hexdigest()


def _gaps(match: JobMatch) -> list[str]:
    return list(dict.fromkeys([
        *match.gaps,
        *(f'{requirement.name}: {requirement.evidence}' for requirement in match.requirements
          if not requirement.covered),
    ]))


def build_cv_readme(
    job: JobPosting, match: JobMatch, tailored: TailoredResume, key: CvVersionKey, requested_at: datetime,
) -> str:
    """A deterministic report of the exact analysis and inputs used for this CV."""
    lines = [
        f'# CV para {job.title} — {job.company}', '',
        f'Oferta: {job.url}', f'Ubicación: {job.location}',
        f'Fecha de solicitud del CV: {requested_at.isoformat()}',
        f'Fecha de publicación: {job.posted_at or "No indicada"}',
        f'Afinidad: {match.score}/100 ({match.verdict})', '',
        '## Requisitos y evidencia', '',
    ]
    for requirement in match.requirements:
        priority = 'imprescindible' if requirement.priority == 'must' else 'deseable'
        coverage = 'cubierto' if requirement.covered else 'brecha'
        lines.append(f'- {requirement.name} ({priority}; {coverage}): {requirement.evidence}')
    if not match.requirements:
        lines.append('- No se detallaron requisitos estructurados.')
    for heading, items in (('Motivos de encaje', match.reasons), ('Brechas', _gaps(match)),
                           ('Foco del ajuste', match.tailoring_focus)):
        lines += ['', f'## {heading}', '', *(f'- {item}' for item in items)]
        if not items:
            lines.append('- No se identificaron.')
    lines += ['', '## Cambios realizados', '', tailored.summary_for_candidate, '']
    lines += [f'- {change.section}: {change.change} Motivo y evidencia: {change.rationale}'
              for change in tailored.changes]
    lines += [
        '', '## Identidad de las entradas', '',
        f'- Oferta: `{key.posting_id}`', f'- Versión: `{key.version_id}`',
        f'- Huella del CV base: `{key.resume_fingerprint}`',
        f'- Huella del perfil: `{key.profile_fingerprint}`',
        f'- Huella de la oferta: `{key.job_fingerprint}`', '',
        'Revisa el PDF antes de postularte. La generación del CV no registra una postulación.', '',
    ]
    return '\n'.join(lines)


def _summary(job: JobPosting, match: JobMatch, tailored: TailoredResume) -> str:
    # Bound each section so long LLM output cannot crowd all gaps out of the message.
    def short(text: str, limit: int = 240) -> str:
        return text if len(text) <= limit else text[:limit - 1] + '…'

    lines = [f'CV listo: {short(job.title)} — {short(job.company)}', '',
             short(tailored.summary_for_candidate, 400), '', 'Motivos de encaje:']
    lines += [f'• {short(reason)}' for reason in match.reasons[:4]] or ['• Sin motivos destacados.']
    lines += ['', 'Brechas:']
    gaps = _gaps(match)
    lines += [f'• {short(gap)}' for gap in gaps[:4]] or ['• No se identificaron brechas.']
    if len(gaps) > 4:
        lines.append(f'• Hay {len(gaps) - 4} brechas adicionales en el informe.')
    lines += ['', 'Revisa el PDF antes de postularte.']
    return '\n'.join(lines)


class GenerateTailoredCv:
    def __init__(
        self, *, resume: ResumeSource, profile_reader: CandidateProfileReader,
        posting_reader: JobPostingReader, matcher: JobMatcher, tailor: ResumeTailor,
        renderer: PdfRenderer, tracking: CvTrackingStore, artifacts: CvArtifactStore,
        delivery: CvDelivery,
    ) -> None:
        self.resume, self.profile_reader, self.posting_reader = resume, profile_reader, posting_reader
        self.matcher, self.tailor, self.renderer = matcher, tailor, renderer
        self.tracking, self.artifacts, self.delivery = tracking, artifacts, delivery

    def prepare(self, posting_id: str, now: datetime) -> PreparedCvRequest:
        """Read and validate inputs, then claim work before the webhook acknowledges it."""
        if now.tzinfo is None or now.utcoffset() is None:
            raise ValueError('CV request time requires an explicit timezone')
        resume_text = self.resume.read()
        if not resume_text or not resume_text.strip():
            raise ValueError('Base resume is empty')
        profile = self.profile_reader.load()
        if profile is None:
            raise LookupError('Candidate profile does not exist')
        posting = self.posting_reader.load(posting_id)
        if posting is None:
            raise LookupError('Job posting does not exist')
        if not posting.description.strip():
            raise ValueError('Job posting description is empty')
        key = CvVersionKey(
            posting_id, _fingerprint(resume_text), _fingerprint(profile.model_dump(mode='json')),
            _fingerprint(posting.model_dump(mode='json')),
        )
        claim = self.tracking.claim(key, now)
        return PreparedCvRequest(
            key=key, action=claim.action, resume_text=resume_text, profile=profile, posting=posting,
            requested_at=now, attempt_id=claim.attempt_id,
        )

    def execute(self, prepared: PreparedCvRequest, chat_id: str, reply_to_message_id: int, *,
                approved_match: JobMatch | None = None, approved_tailored: TailoredResume | None = None,
                resend: bool = True) -> CvGenerationResult:
        if prepared.action == 'in_progress':
            return CvGenerationResult(prepared.key, 'PROCESSING', 'PENDING')
        key = prepared.key
        if prepared.action == 'generate':
            if not prepared.attempt_id:
                raise ValueError('CV generation requires a claimed attempt')
            try:
                match = approved_match or self.matcher.score(prepared.posting, prepared.profile, prepared.resume_text)
                tailored = approved_tailored or self.tailor.tailor(prepared.posting, match, prepared.profile, prepared.resume_text)
                if not tailored.resume_markdown.strip():
                    raise ValueError('Tailored resume is empty')
                pdf = self.renderer.render(tailored.resume_markdown)
                if not isinstance(pdf, bytes) or not pdf:
                    raise ValueError('PDF renderer returned empty output')
                artifacts = self.artifacts.save(
                    key, pdf, tailored.resume_markdown,
                    build_cv_readme(prepared.posting, match, tailored, key, prepared.requested_at),
                    attempt_id=prepared.attempt_id,
                )
                self.tracking.mark_ready(key, artifacts, match, tailored, attempt_id=prepared.attempt_id)
            except Exception:
                self.tracking.mark_failed(key, attempt_id=prepared.attempt_id)
                raise

        # A Telegram or download failure must never invalidate complete generation artifacts.
        try:
            ready = self.tracking.load_ready(key)
            if ready.delivery_status == 'SENT' and resend:
                self.tracking.begin_delivery(key)
                ready = self.tracking.load_ready(key)
            if ready.summary_message_id is None:
                message_id = self.delivery.send_summary(
                    chat_id, reply_to_message_id, _summary(prepared.posting, ready.match, ready.tailored),
                )
                self.tracking.mark_summary_sent(key, message_id)
            if ready.pdf_message_id is None:
                pdf = self.artifacts.read_pdf(ready.artifacts)
                message_id = self.delivery.send_pdf(chat_id, reply_to_message_id, pdf, key.posting_id)
                self.tracking.mark_pdf_sent(key, message_id)
            elif ready.summary_message_id is not None and ready.delivery_status == 'FAILED':
                # A receipt can commit despite a transport error observed by its caller.
                self.tracking.mark_pdf_sent(key, ready.pdf_message_id)
        except Exception:
            self.tracking.mark_delivery_failed(key)
            raise
        return CvGenerationResult(key, 'READY', 'SENT')
