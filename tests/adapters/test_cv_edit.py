from types import SimpleNamespace
import json
import pytest


def test_interpreter_structured_targeted_proposal(profile):
    from job_agent.adapters.llm.cv_edit import CvEditInterpreter
    from job_agent.application.cv_review_models import CvEditProposal, TextReplacement
    from job_agent.domain.cv_corrections import CorrectionSet, FactOperation
    calls = []
    proposal = CvEditProposal(replacements=[TextReplacement(old_text='English B2', new_text='')],
        fact_operations=[FactOperation('remove_language', 'English')], explanation='Eliminar dato falso')
    model = SimpleNamespace(complete=lambda **kw: calls.append(kw) or proposal)
    assert CvEditInterpreter(model).propose('# CV\nEnglish B2', 'No hablo inglés', profile,
        CorrectionSet(version=0, operations=[])) == proposal
    assert calls[0]['schema'] is CvEditProposal
    assert 'English B2' in calls[0]['content'][0]['text']


def test_interpreter_uses_structured_provider_content_blocks(profile, monkeypatch):
    """The provider must accept an edit request before it reaches the network."""
    from job_agent.adapters.llm.cv_edit import CvEditInterpreter
    from job_agent.adapters.llm.openrouter_model import OpenRouterStructuredModel
    from job_agent.domain.cv_corrections import CorrectionSet

    provider = OpenRouterStructuredModel('test/model', 'unused-test-key')
    messages_seen = []

    def answer(messages, schema, effort, max_tokens):
        messages_seen.extend(messages)
        return json.dumps({
            'proposal_id': None,
            'replacements': [{'old_text': 'English B2', 'new_text': ''}],
            'fact_operations': [],
            'explanation': 'Elimina el dato falso',
        })

    monkeypatch.setattr(provider, '_request', answer)
    proposal = CvEditInterpreter(provider).propose(
        '# CV\nEnglish B2', 'No hablo inglés', profile,
        CorrectionSet(version=0, operations=[]),
    )

    assert proposal.replacements[0].old_text == 'English B2'
    assert messages_seen[1]['content'][0]['text'].count('No hablo inglés') == 1
