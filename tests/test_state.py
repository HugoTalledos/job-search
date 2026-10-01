from job_agent.state import State, job_key


def test_key_uses_external_id_and_ignores_tracking(job):
    other = job.model_copy(update={"url": "https://linkedin.com/jobs/view/123"})
    assert job_key(job) == job_key(other)
    no_id = job.model_copy(update={"external_id": ""})
    assert job_key(no_id) == job_key(no_id.model_copy(update={"url": job.url.split("?")[0]}))


def test_seen_roundtrip_and_repost_detection(tmp_path, job):
    state = State(tmp_path / "state.json")
    assert not state.is_seen(job)
    state.mark(job, 80)
    state.save()
    reloaded = State(tmp_path / "state.json")
    assert reloaded.is_seen(job)
    repost = job.model_copy(update={"external_id": "999", "url": "https://x/999"})
    assert reloaded.is_seen(repost)
    assert reloaded.seen_urls() == [job.url]
