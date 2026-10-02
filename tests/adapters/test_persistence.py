from datetime import datetime, timedelta, timezone

import pytest
from pathlib import Path

from job_agent.adapters.persistence import (
    FileSystemApplicationStore,
    JsonlMatchHistory,
    JsonProfileStore,
    JsonSeenJobsRepository,
)
from job_agent.domain.models import MatchRecord, StoredProfile
from job_agent.domain.policies import job_key


def test_seen_repository_roundtrip_and_repost_detection(tmp_path, job):
    repo = JsonSeenJobsRepository(tmp_path / "state.json")
    assert not repo.is_seen_key(job_key(job))
    repo.mark(job, outcome="puntuada", score=80)
    repo.commit()
    reloaded = JsonSeenJobsRepository(tmp_path / "state.json")
    assert reloaded.is_seen_key(job_key(job))
    assert reloaded.seen[job_key(job)]["outcome"] == "puntuada"
    assert not reloaded.is_duplicate(job)  # same id is not "another id"
    repost = job.model_copy(update={"external_id": "999", "url": "https://x/999", "company": "ACME & Co."})
    assert reloaded.is_duplicate(repost)


def test_seen_repository_forgets_only_postings_not_seen_for_a_while(tmp_path, job):
    path = tmp_path / "state.json"
    repo = JsonSeenJobsRepository(path, retention_days=30)
    old = job.model_copy(update={"external_id": "old", "url": "https://x/old"})
    repo.mark(job, outcome="puntuada")
    repo.mark(old, outcome="puntuada")
    long_ago = (datetime.now(timezone.utc) - timedelta(days=60)).isoformat()
    for entry in repo.seen.values():
        entry["first_seen"] = entry["last_seen"] = long_ago
    assert repo.is_seen_key(job_key(job))  # still showing up in searches -> last_seen refreshed
    repo.commit()
    assert set(JsonSeenJobsRepository(path).seen) == {job_key(job)}


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
    store = FileSystemApplicationStore(tmp_path / "anywhere" / "resumes")  # any folder, inside or outside the repo
    assert store.list_versions() == []

    saved = store.save(job, match, tailored, base_fingerprint="fp1")
    folder = tmp_path / "anywhere" / "resumes" / saved.version_id
    assert saved.folder == str(folder.resolve()) and saved.link is None
    assert (folder / "resume.md").read_text() == tailored.resume_markdown
    assert "Enfocado a backend" in (folder / "README.md").read_text()
    assert saved.attachment.name in ("resume.pdf", "resume.md")

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
    assert f"Adaptada a partir de: `{version.id}`" in (Path(adapted.folder) / "README.md").read_text()


def test_application_store_rejects_paths_outside_output(tmp_path):
    store = FileSystemApplicationStore(tmp_path / "output")
    with pytest.raises(ValueError):
        store.load_markdown("../etc")
