"""Evaluate each pending offer independently and save successful scores."""

from __future__ import annotations

import logging
from dataclasses import dataclass

from .ports import PendingPostingStore, ProfileReader, ScoringTool

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class ScoreReport:
    evaluated: int = 0
    failed: int = 0


class ScorePendingJobs:
    def __init__(
        self,
        profile_reader: ProfileReader,
        postings: PendingPostingStore,
        scorer: ScoringTool,
    ) -> None:
        self.profile_reader = profile_reader
        self.postings = postings
        self.scorer = scorer

    def execute(self) -> ScoreReport:
        profile = self.profile_reader.load()
        pending = self.postings.list_pending()
        evaluated = failed = 0
        for posting in pending:
            try:
                result = self.scorer.score(profile, posting.job)
                self.postings.mark_evaluated(posting.document_id, result)
                evaluated += 1
            except Exception as exc:
                failed += 1
                log.error("Failed to evaluate posting %s (%s)", posting.document_id, type(exc).__name__)
        return ScoreReport(evaluated=evaluated, failed=failed)
