"""Composition root: the only place that knows which adapter implements each port."""

from __future__ import annotations

import os
from dataclasses import dataclass

from .adapters.code_repositories import GitRepositoryReader
from .adapters.job_sources import LinkedInMcpJobSource, McpServerParams
from .adapters.llm import ClaudeJobMatcher, ClaudeProfileInferer, ClaudeResumeSelector, ClaudeResumeTailor
from .adapters.notifications import ConsoleNotifier, TelegramNotifier
from .adapters.persistence import (
    FileSystemApplicationStore,
    JsonlMatchHistory,
    JsonProfileStore,
    JsonSeenJobsRepository,
)
from .adapters.resume import FileResumeSource
from .application import EnsureProfile, RunSearchCycle
from .application.ports import JobSource, Notifier
from .config import ROOT, Config
from .domain.models import RepoRef
from .domain.policies import JobFilter, MatchingPolicy, ReusePolicy, SearchPreferences


@dataclass
class Container:
    ensure_profile: EnsureProfile
    run_search_cycle: RunSearchCycle
    notifier: Notifier


def build_job_sources(cfg: Config) -> list[JobSource]:
    sources: list[JobSource] = []
    linkedin = cfg.search.sources.linkedin
    if linkedin.enabled:
        server = McpServerParams(name="linkedin", command=linkedin.command, args=linkedin.args, env=linkedin.env)
        sources.append(LinkedInMcpJobSource(server, max_pages=linkedin.max_pages))
    return sources


def build_notifier(dry_run: bool) -> Notifier:
    token, chat_id = os.environ.get("TELEGRAM_BOT_TOKEN"), os.environ.get("TELEGRAM_CHAT_ID")
    if dry_run or not (token and chat_id):
        return ConsoleNotifier()
    return TelegramNotifier(token, chat_id)


def _web_url_base() -> str | None:
    server, repo, ref = (os.environ.get(k) for k in ("GITHUB_SERVER_URL", "GITHUB_REPOSITORY", "GITHUB_REF_NAME"))
    return f"{server}/{repo}/blob/{ref}" if server and repo and ref else None


def build_container(cfg: Config, *, dry_run: bool = False) -> Container:
    data = ROOT / "data"
    resume = FileResumeSource(cfg.resume_file)
    ensure_profile = EnsureProfile(
        resume=resume,
        repositories=GitRepositoryReader(
            repositories=[RepoRef(url=r.url, branch=r.branch) for r in cfg.repositories],
            github_user=cfg.github_user,
            include_forks=cfg.include_forks,
            max_repos=cfg.max_repos,
        ),
        inferer=ClaudeProfileInferer(),
        store=JsonProfileStore(data / "profile.json"),
        refresh_days=cfg.profile_refresh_days,
        preferred_locations=cfg.search.locations,
    )
    notifier = build_notifier(dry_run)
    run_search_cycle = RunSearchCycle(
        ensure_profile=ensure_profile,
        resume=resume,
        sources=build_job_sources(cfg),
        matcher=ClaudeJobMatcher(),
        tailor=ClaudeResumeTailor(),
        selector=ClaudeResumeSelector(),
        applications=FileSystemApplicationStore(ROOT / "output", ROOT, _web_url_base()),
        notifier=notifier,
        seen=JsonSeenJobsRepository(data / "state.json"),
        history=JsonlMatchHistory(data / "matches.jsonl"),
        preferences=SearchPreferences(
            locations=tuple(cfg.search.locations),
            extra_keywords=tuple(cfg.search.extra_keywords),
            max_roles_from_profile=cfg.search.max_roles_from_profile,
            max_queries=cfg.search.max_queries,
            posted_within_days=cfg.search.posted_within_days,
            work_types=tuple(cfg.search.work_types),
            experience_levels=tuple(cfg.search.experience_levels),
            max_details_per_run=cfg.search.max_details_per_run,
            max_jobs_per_run=cfg.search.max_jobs_per_run,
        ),
        job_filter=JobFilter(
            exclude_companies=tuple(cfg.search.exclude_companies),
            exclude_title_keywords=tuple(cfg.search.exclude_title_keywords),
            posted_within_days=cfg.search.posted_within_days,
            work_types=tuple(cfg.search.work_types),
        ),
        policy=MatchingPolicy(cfg.matching.min_score_to_notify, cfg.matching.min_score_to_tailor),
        reuse=ReusePolicy(cfg.resume_reuse.enabled, cfg.resume_reuse.max_candidates),
    )
    return Container(ensure_profile=ensure_profile, run_search_cycle=run_search_cycle, notifier=notifier)
