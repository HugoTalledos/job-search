import pytest
from pydantic import ValidationError

from job_agent.domain.cv_corrections import (
    CorrectionSet, FactOperation, apply_fact_operations, contradictions,
)
from job_agent.domain.models import Profile, Skill


def profile():
    return Profile(full_name='Candidate', headline='Developer', seniority='mid',
                   years_of_experience=4, summary='Developer', target_roles=[],
                   search_keywords=[], skills=[Skill(name='Python', level='advanced', evidence='Repo')],
                   domains=[], languages=['English (B2)', 'Español (nativo)'], locations=[],
                   notable_projects=[], strengths_missing_from_resume=[])


def test_denied_english_removes_alias_and_preserves_original():
    base = profile()
    result = apply_fact_operations(base, [FactOperation('remove_language', 'inglés')])
    assert result.languages == ['Español (nativo)']
    assert base.languages == ['English (B2)', 'Español (nativo)']


def test_skill_level_replacement_is_pure():
    base = profile()
    result = apply_fact_operations(base, [FactOperation('set_skill_level', 'python', 'basic')])
    assert result.skills == [Skill(name='Python', level='basic', evidence='Repo')]
    assert base.skills[0].level == 'advanced'


def test_ordered_operations_restore_language_and_update_validated_fields():
    result = apply_fact_operations(profile(), [
        FactOperation('remove_language', 'en'), FactOperation('set_language', 'English', 'C1'),
        FactOperation('set_seniority', 'seniority', 'senior'),
        FactOperation('set_years_of_experience', 'years_of_experience', 6),
        FactOperation('remove_skill', 'python'),
    ])
    assert result.languages == ['Español (nativo)', 'English (C1)']
    assert result.skills == []
    assert result.seniority == 'senior'
    assert result.years_of_experience == 6


@pytest.mark.parametrize('markdown', ['## Idiomas\nInglés: B2', 'English (B2)', 'INGLES avanzado'])
def test_language_denial_detects_cv_aliases(markdown):
    assert contradictions(markdown, [FactOperation('remove_language', 'english')])


def test_whole_terms_do_not_confuse_b2b_or_pythonic_with_denied_facts():
    assert contradictions('Ventas B2B y Pythonic code', [
        FactOperation('remove_language', 'english'), FactOperation('remove_skill', 'Python'),
        FactOperation('deny_claim', 'B2'),
    ]) == []


def test_later_replacement_supersedes_earlier_denial():
    assert contradictions('English (C1)', [FactOperation('remove_language', 'english'),
                                           FactOperation('set_language', 'english', 'C1')]) == []
    assert contradictions('English (B2)', [FactOperation('set_language', 'english', 'C1')])


@pytest.mark.parametrize('args', [
    ('unsupported', 'english'), ('remove_language', 'English or French'),
    ('remove_language', 'english', 'B2'), ('set_language', 'english', 'maybe'),
    ('set_skill_level', 'Python', 'wizard'), ('set_seniority', 'skills', 'senior'),
    ('set_years_of_experience', 'years_of_experience', -1),
    ('set_years_of_experience', 'years_of_experience', float('nan')),
    ('deny_claim', ''), ('revoke', ''),
])
def test_invalid_operations_rejected_before_persistence(args):
    with pytest.raises((ValueError, ValidationError)):
        FactOperation(*args)


def test_correction_set_validates_version_and_roundtrips_operations():
    corrections = CorrectionSet(version=2, operations=[FactOperation('remove_language', 'inglés')])
    assert CorrectionSet.model_validate(corrections.model_dump()).operations[0].subject == 'english'
    with pytest.raises(ValidationError):
        CorrectionSet(version=-1, operations=[])


def test_revocation_must_be_resolved_by_store_before_projection():
    with pytest.raises(ValueError):
        apply_fact_operations(profile(), [FactOperation('revoke', 'prior-correction')])


def test_skill_update_requires_an_unambiguous_existing_skill():
    with pytest.raises(ValueError):
        apply_fact_operations(profile(), [FactOperation('set_skill_level', 'Java', 'basic')])


def test_short_denied_claim_matches_only_whole_term():
    assert contradictions('Nivel B2', [FactOperation('deny_claim', 'B2')]) == ['b2']
    assert contradictions('Ventas B2B', [FactOperation('deny_claim', 'B2')]) == []


def test_language_alias_denial_matches_canonical_cv_language():
    assert contradictions('English (B2)', [FactOperation('deny_claim', 'inglés')]) == ['english']
