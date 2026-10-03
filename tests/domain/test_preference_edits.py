import pytest
from job_contracts import SearchPreferences

from job_agent.domain.preference_edits import (
    PreferenceEdit,
    PreferenceOperation,
    apply_operations,
    preference_diff,
)


def op(action, field, values, explanation=""):
    return PreferenceOperation(action=action, field=field, values=values, explanation=explanation)


def test_add_and_remove_keep_other_fields():
    current = SearchPreferences(keywords_include=["Python"], locations=["Remote", "Colombia"], version=3)
    edit = PreferenceEdit(operations=[op("add", "keywords_include", ["Go"]), op("remove", "locations", ["colombia"])])
    out = apply_operations(current, edit)
    assert out.preferences.keywords_include == ["Python", "Go"] and out.preferences.locations == ["Remote"]
    assert out.preferences.version == 3 and out.problems == []
    assert current.locations == ["Remote", "Colombia"]


def test_remove_companies_uses_company_normalisation():
    current = SearchPreferences(exclude_companies=["Acme Inc."])
    out = apply_operations(current, PreferenceEdit(operations=[op("remove", "exclude_companies", ["acme inc"])]))
    assert out.preferences.exclude_companies == []


def test_set_scalars_and_lists():
    edit = PreferenceEdit(operations=[
        op("set", "posted_within_days", ["7"]), op("set", "use_profile_keywords", ["false"]),
        op("set", "work_types", ["remote", "hybrid"]),
    ])
    out = apply_operations(SearchPreferences(work_types=["on_site"]), edit)
    assert out.preferences.posted_within_days == 7 and out.preferences.use_profile_keywords is False
    assert out.preferences.work_types == ["remote", "hybrid"]


@pytest.mark.parametrize("bad", [op("add", "posted_within_days", ["3"]), op("set", "posted_within_days", ["40"]),
                                 op("set", "posted_within_days", ["x"]),
                                 op("set", "work_types", ["office"]), op("set", "use_profile_keywords", ["quizás"]),
                                 op("set", "none", [])])
def test_invalid_operation_discards_whole_edit(bad):
    out = apply_operations(SearchPreferences(), PreferenceEdit(operations=[op("add", "keywords_include", ["Go"]), bad]))
    assert out.preferences is None and out.problems


def test_unclear_only_and_no_change_create_nothing():
    unclear = apply_operations(SearchPreferences(), PreferenceEdit(operations=[op("unclear", "none", [], "No entendí")]))
    assert unclear.preferences is None and "No entendí" in unclear.problems
    same = apply_operations(SearchPreferences(keywords_include=["Go"]),
                            PreferenceEdit(operations=[op("add", "keywords_include", ["go"])]))
    assert same.preferences is None and same.problems == ["No encontré cambios para aplicar."]


def test_diff_lists_added_removed_and_scalars():
    old = SearchPreferences(keywords_include=["Python"], locations=["Colombia"])
    new = SearchPreferences(keywords_include=["Python", "Rust", "Go"], locations=[], posted_within_days=7,
                            use_profile_keywords=False)
    assert preference_diff(old, new) == [
        "➕ Palabras clave: Rust, Go",
        "🔁 Usar keywords del perfil: no",
        "➖ Ubicaciones: Colombia",
        "🗓 Días desde la publicación: 7",
    ]
