"""Enrich, evaluate, and notify each pending offer independently."""

from __future__ import annotations

import logging
from dataclasses import dataclass

from .ports import (OfferEnricher, OfferNotifier, PendingNotificationStore, PendingPostingStore,
                    ProfileReader, ScoringTool)

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class ScoreReport:
    evaluated: int = 0
    failed: int = 0
    notified: int = 0


@dataclass(frozen=True)
class ResendReport:
    pending: int = 0
    notified: int = 0
    failed: int = 0


class ResendPendingNotifications:
    def __init__(self, postings: PendingNotificationStore, notifier: OfferNotifier) -> None:
        self.postings = postings
        self.notifier = notifier

    def execute(self) -> ResendReport:
        pending = self.postings.list_pending_notifications()
        notified = failed = 0
        for posting in pending:
            try:
                self.notifier.notify(posting.job, posting.result, posting.enrichment)
                self.postings.mark_notified(posting.document_id)
                notified += 1
            except Exception as exc:
                failed += 1
                log.error("Failed to resend posting %s (%s)", posting.document_id, type(exc).__name__)
        return ResendReport(pending=len(pending), notified=notified, failed=failed)


class ScorePendingJobs:
    def __init__(
        self,
        profile_reader: ProfileReader,
        postings: PendingPostingStore,
        scorer: ScoringTool,
        enricher: OfferEnricher,
        notifier: OfferNotifier,
        min_score_to_notify: int,
    ) -> None:
        self.profile_reader = profile_reader
        self.postings = postings
        self.scorer = scorer
        self.enricher = enricher
        self.notifier = notifier
        self.min_score_to_notify = min_score_to_notify

    def execute(self) -> ScoreReport:
        profile = self.profile_reader.load()
        pending = self.postings.list_pending()
        evaluated = failed = notified = 0
        for posting in pending:
            try:
                enrichment = posting.enrichment
                if enrichment is None:
                    enrichment = self.enricher.enrich(posting.job)
                    self.postings.mark_enriched(posting.document_id, enrichment)
                result = self.scorer.score(profile, posting.job)
                should_notify = result.score >= self.min_score_to_notify
                self.postings.mark_scored(posting.document_id, result, notify=should_notify)
                evaluated += 1
                if should_notify:
                    self.notifier.notify(posting.job, result, enrichment)
                    self.postings.mark_notified(posting.document_id)
                    notified += 1
            except Exception as exc:
                failed += 1
                log.error("Failed to evaluate posting %s (%s)", posting.document_id, type(exc).__name__)
        return ScoreReport(evaluated=evaluated, failed=failed, notified=notified)
