from datetime import datetime, timezone

from job_agent.adapters.persistence import (
    FileSystemApplicationStore,
    JsonlMatchHistory,
    JsonProfileStore,
    JsonSeenJobsRepository,
)
from job_agent.domain.models import MatchRecord, StoredProfile


def test_seen_repository_roundtrip_and_repost_detection(tmp_path, job):
    repo = JsonSeenJobsRepository(tmp_path / "state.json")
    assert not repo.is_seen(job)
    repo.mark(job, 80)
    repo.commit()
    reloaded = JsonSeenJobsRepository(tmp_path / "state.json")
    assert reloaded.is_seen(job)
    assert reloaded.is_seen(job.model_copy(update={"external_id": "999", "url": "https://x/999"}))
    assert reloaded.seen_urls() == [job.url]


def test_profile_store_roundtrip(tmp_path, profile):
    store = JsonProfileStore(tmp_path / "p.json")
    assert store.load() is None
    stored = StoredProfile(profile=profile, fingerprint="f", built_at=datetime.now(timezone.utc))
    store.save(stored)
    assert store.load() == stored


def test_match_history_appends(tmp_path):
    history = JsonlMatchHistory(tmp_path / "m.jsonl")
    rec = MatchRecord(at=datetime.now(timezone.utc), key="k", title="t", company="c", url="u", source="s",
                      score=1, verdict="no", tailored=False)
    history.append(rec)
    history.append(rec)
    assert len((tmp_path / "m.jsonl").read_text().splitlines()) == 2


def test_application_store_writes_resume_and_readme(tmp_path, job, match, tailored):
    store = FileSystemApplicationStore(tmp_path / "output", tmp_path, "https://github.com/o/r/blob/main")
    saved = store.save(job, match, tailored)
    folder = tmp_path / saved.folder
    assert (folder / "resume.md").read_text() == tailored.resume_markdown
    assert "Enfocado a backend" in (folder / "README.md").read_text()
    assert saved.attachment.name in ("resume.pdf", "resume.md")
    assert saved.link == f"https://github.com/o/r/blob/main/{saved.folder}/resume.md"
