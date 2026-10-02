from datetime import datetime, timezone

import pytest

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


def test_application_store_catalogue(tmp_path, job, match, tailored):
    store = FileSystemApplicationStore(tmp_path / "output", tmp_path, "https://github.com/o/r/blob/main")
    assert store.list_versions() == []

    saved = store.save(job, match, tailored, base_fingerprint="fp1")
    folder = tmp_path / saved.folder
    assert (folder / "resume.md").read_text() == tailored.resume_markdown
    assert "Enfocado a backend" in (folder / "README.md").read_text()
    assert saved.attachment.name in ("resume.pdf", "resume.md")
    assert saved.link == f"https://github.com/o/r/blob/main/{saved.version_id}/resume.md"

    [version] = store.list_versions()
    assert version.id == saved.version_id and version.base_fingerprint == "fp1"
    assert version.language == "es" and version.highlights == ["Python", "AWS"]
    assert store.load_markdown(version.id) == tailored.resume_markdown
    assert store.locate(version.id) == saved

    other = job.model_copy(update={"external_id": "77", "title": "Platform Engineer", "company": "Globex"})
    store.record_use(version.id, other)
    assert [u.company for u in store.list_versions()[0].used_for] == ["Acme & Co", "Globex"]

    adapted = store.save(other, match, tailored, base_fingerprint="fp1", adapted_from=version.id)
    assert len(store.list_versions()) == 2
    assert f"Adaptada a partir de: `{version.id}`" in (tmp_path / adapted.folder / "README.md").read_text()


def test_application_store_rejects_paths_outside_output(tmp_path):
    store = FileSystemApplicationStore(tmp_path / "output", tmp_path)
    with pytest.raises(ValueError):
        store.load_markdown("../etc")
