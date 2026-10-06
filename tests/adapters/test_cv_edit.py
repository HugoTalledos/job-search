from types import SimpleNamespace
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
    assert 'English B2' in calls[0]['content']
