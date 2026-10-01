import json

from job_agent import pipeline, state
from job_agent.config import Config


def test_run_notifies_tailors_and_dedupes(tmp_path, monkeypatch, job, profile, match, tailored):
    resume = tmp_path / "base.md"
    resume.write_text("# Test")
    monkeypatch.setattr(pipeline, "ROOT", tmp_path)
    monkeypatch.setattr(pipeline, "OUTPUT_DIR", tmp_path / "output")
    monkeypatch.setattr(state, "STATE_PATH", tmp_path / "data" / "state.json")
    monkeypatch.setattr(state, "HISTORY_PATH", tmp_path / "data" / "matches.jsonl")
    monkeypatch.setattr(pipeline, "ensure_profile", lambda cfg: profile)

    weak = job.model_copy(update={"external_id": "456", "title": "Chef", "url": "https://x/456"})

    async def fake_search(p, cfg, seen):
        return [job, job, weak]

    monkeypatch.setattr(pipeline, "search_jobs", fake_search)
    monkeypatch.setattr(pipeline, "score_job", lambda j, p, r: match if j.title != "Chef" else match.model_copy(update={"score": 10}))
    monkeypatch.setattr(pipeline, "tailor_resume", lambda *a: tailored)
    sent = []
    monkeypatch.setattr(pipeline.Notifier, "send", lambda self, text, att=None: sent.append((text, att)))

    cfg = Config(resume_path=str(resume))
    assert pipeline.run(cfg) == 1
    assert len(sent) == 1 and sent[0][1].name in ("resume.pdf", "resume.md")
    out = list((tmp_path / "output").rglob("README.md"))
    assert out and "Enfocado a backend" in out[0].read_text()
    history = [json.loads(l) for l in (tmp_path / "data" / "matches.jsonl").read_text().splitlines()]
    assert [h["score"] for h in history] == [82, 10]

    # Second run: everything already seen -> no notifications.
    sent.clear()
    assert pipeline.run(cfg) == 0 and not sent
