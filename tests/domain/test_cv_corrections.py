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


@pytest.mark.parametrize('markdown, operation', [
    ('Languages: English C1; French B2', FactOperation('set_language', 'english', 'C1')),
    ('Languages: English C1, French B2', FactOperation('set_language', 'english', 'C1')),
    ('Skills: Python advanced; Java basic', FactOperation('set_skill_level', 'Python', 'advanced')),
])
def test_substitution_checks_only_the_named_fact_segment(markdown, operation):
    assert contradictions(markdown, [operation]) == []


def test_segmented_wrong_level_still_contradicts():
    assert contradictions('Languages: French C1; English B2', [
        FactOperation('set_language', 'english', 'C1')]) == ['english']


@pytest.mark.parametrize('kind', ['remove_skill', 'set_skill_level'])
@pytest.mark.parametrize('subject', ['Python or Java', 'Python o Java', 'Python/Java', 'Python, Java', 'Python and Java'])
def test_skill_operation_rejects_multiple_or_ambiguous_targets(kind, subject):
    with pytest.raises(ValidationError):
        FactOperation(kind, subject, 'basic' if kind == 'set_skill_level' else None)


@pytest.mark.parametrize('value', [True, False])
def test_experience_rejects_boolean_before_numeric_coercion(value):
    with pytest.raises(ValidationError):
        FactOperation('set_years_of_experience', 'years_of_experience', value)


def test_skill_update_rejects_duplicate_normalized_skill_names():
    base = profile()
    base.skills.append(Skill(name='PYTHON', level='basic', evidence='Other repo'))
    with pytest.raises(ValueError):
        apply_fact_operations(base, [FactOperation('set_skill_level', 'python', 'expert')])


def test_markdown_table_row_associates_fact_with_level_cell():
    operation = FactOperation('set_language', 'english', 'C1')
    assert contradictions('| Language | Level |\n| English | B2 |\n| French | C1 |', [operation]) == ['english']
    assert contradictions('| English | C1 |\n| French | B2 |', [operation]) == []


def test_skill_target_rejects_newline_before_normalization():
    with pytest.raises(ValidationError):
        FactOperation('remove_skill', 'Python\nJava')
