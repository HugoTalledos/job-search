import json

import httpx
import pytest

from job_agent.scoring.models import PostingEnrichment


def make_tool(payload, seen, status=200):
    from job_agent.scoring.enrichment import JevOfferEnricher

    def handler(request):
        seen.append(request)
        return httpx.Response(status, json=payload)

    return JevOfferEnricher("or-key", client=httpx.Client(transport=httpx.MockTransport(handler)))


def choice(value, confidence=0.95, kind="choice"):
    return {"type": kind, "choice": value, "confidence": confidence}


def test_extracts_literal_language_and_salary_with_separate_jev_questions(job):
    description = "Required: English B2. Salary: USD 2,000 - 3,000 per month."
    offer = job.model_copy(update={"description": description})
    seen = []
    tool = make_tool({"answers": {
        "required_language": choice("candidate_0"),
        "salary_range": choice("candidate_0"),
    }}, seen)

    result = tool.enrich(offer)

    assert result == PostingEnrichment("Required: English B2.", "Salary: USD 2,000 - 3,000 per month.")
    [request] = seen
    assert str(request.url) == "https://openrouter.ai/api/alpha/decisions"
    assert request.headers["authorization"] == "Bearer or-key"
    body = json.loads(request.content)
    assert body["model"] == "typesafe/jev-1.13"
    assert body["state"] == {"description": description}
    assert set(body["questions"]) == {"required_language", "salary_range"}
    assert all(question["type"] == "choice" for question in body["questions"].values())
    assert set(body["questions"]["required_language"]["criteria"]) == {"candidate_0", "none"}
    assert "English B2" in body["questions"]["required_language"]["criteria"]["candidate_0"]
    assert "USD 2,000 - 3,000" in body["questions"]["salary_range"]["criteria"]["candidate_0"]


def test_spanish_candidates_split_by_newlines(job):
    description = "Se requiere inglés C1\nRango salarial: COP 8.000.000 - 10.000.000 mensuales"
    offer = job.model_copy(update={"description": description})

    result = make_tool({"answers": {
        "required_language": choice("candidate_0"),
        "salary_range": choice("candidate_0"),
    }}, []).enrich(offer)

    assert result.required_language == "Se requiere inglés C1"
    assert result.salary_range == "Rango salarial: COP 8.000.000 - 10.000.000 mensuales"


def test_long_sentence_keeps_literal_fragment_with_language(job):
    description = ("Una descripción extensa " * 12) + "English B2 required."
    offer = job.model_copy(update={"description": description})
    seen = []

    make_tool({"answers": {"required_language": choice("candidate_0")}}, seen).enrich(offer)

    body = json.loads(seen[0].content)
    assert "English B2" in body["questions"]["required_language"]["criteria"]["candidate_0"]


def test_no_candidates_skips_jev_call(job):
    result = make_tool({}, []).enrich(job)
    assert result == PostingEnrichment()


def test_jev_none_omits_preferred_language_and_unrelated_salary(job):
    description = "English is a plus. Previous salary: $2,000 - $3,000."
    offer = job.model_copy(update={"description": description})
    seen = []

    result = make_tool({"answers": {
        "required_language": choice("none"),
        "salary_range": choice("none"),
    }}, seen).enrich(offer)

    assert result == PostingEnrichment()
    assert len(seen) == 1


def test_single_salary_amount_is_not_a_range_candidate(job):
    offer = job.model_copy(update={"description": "Pay: USD 2,000 per month."})
    result = make_tool({}, []).enrich(offer)
    assert result.salary_range is None


@pytest.mark.parametrize("answer", [
    choice("candidate_0", kind="score"),
    choice("candidate_99"),
    choice("candidate_0", confidence="0.95"),
])
def test_invalid_jev_answer_is_rejected(job, answer):
    offer = job.model_copy(update={"description": "English B2 required."})
    with pytest.raises(ValueError):
        make_tool({"answers": {"required_language": answer}}, []).enrich(offer)


def test_low_confidence_omits_candidate(job):
    offer = job.model_copy(update={"description": "English B2 required."})
    result = make_tool({"answers": {"required_language": choice("candidate_0", 0.79)}}, []).enrich(offer)
    assert result == PostingEnrichment()


def test_http_failure_propagates(job):
    offer = job.model_copy(update={"description": "English B2 required."})
    with pytest.raises(httpx.HTTPStatusError):
        make_tool({"error": "down"}, [], status=503).enrich(offer)
