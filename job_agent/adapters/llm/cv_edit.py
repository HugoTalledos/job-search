"""Interpret instructions as explicit fragments; never generate a replacement CV."""
import json
from . import prompts
from ...application.cv_review_models import CvEditProposal


class CvEditInterpreter:
    def __init__(self, model):
        self.model = model

    def propose(self, markdown, instruction, profile, corrections):
        return self.model.complete(system=prompts.CV_EDIT_SYSTEM,
            content=json.dumps({'markdown': markdown, 'instruction': instruction,
                'profile': profile.model_dump(mode='json'),
                'corrections': corrections.model_dump(mode='json')}, ensure_ascii=False),
            schema=CvEditProposal, effort='low', max_tokens=8000)
