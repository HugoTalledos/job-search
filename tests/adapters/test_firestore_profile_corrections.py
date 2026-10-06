import pytest
from job_agent.adapters.persistence.firestore_profile_corrections import FirestoreProfileCorrections, CorrectionVersionConflict
from job_agent.adapters.persistence.firestore_profile import FirestoreProfileStore
from job_agent.domain.cv_corrections import FactOperation
from job_agent.domain.models import StoredProfile
from job_agent.scoring.firestore import FirestoreScoringStore
from tests.adapters.test_firestore_cv_tracking import Client
from tests.adapters.test_firestore_profile import BUILT


def test_confirm_migrates_projects_revokes_and_is_idempotent(profile):
    client = Client()
    base = profile.model_copy(update={'languages': ['English B2', 'Spanish native']})
    client.docs['profiles/current'] = {**base.model_dump(), 'resume_fingerprint': 'base', 'built_at': BUILT}
    store = FirestoreProfileCorrections(client)
    operations = [FactOperation('remove_language', 'English')]
    first = store.confirm(operations, 0)
    assert first.version == 1
    assert store.confirm(operations, 0) == first
    with pytest.raises(CorrectionVersionConflict):
        store.confirm([FactOperation('remove_language', 'Spanish')], 0)
    data = client.docs['profiles/current']
    assert data['inferred_profile'] == base.model_dump()
    assert data['resume_fingerprint'] == 'base'
    assert FirestoreScoringStore(client).load().languages == ['Spanish native']
    correction_id = client.docs['profile_corrections/current']['active_ids'][0]
    store.confirm([FactOperation('revoke', correction_id)], 1)
    assert store.load().operations == []
    assert FirestoreScoringStore(client).load() == base
    assert client.docs['profile_corrections/' + correction_id]['operation'] == operations[0].model_dump()


def test_save_inferred_reapplies_latest_correction(profile):
    client = Client()
    profiles = FirestoreProfileStore(client)
    profiles.save(StoredProfile(profile=profile, built_at=BUILT))
    corrections = FirestoreProfileCorrections(client)
    corrections.confirm([FactOperation('remove_language', 'English')], 0)
    inferred = profile.model_copy(update={'languages': ['English B2']})
    effective = profiles.save_inferred(StoredProfile(profile=inferred, built_at=BUILT))
    assert effective.profile.languages == []
    assert profiles.load() == effective
    assert client.docs['profiles/current']['inferred_profile']['languages'] == ['English B2']
    assert client.docs['profiles/current']['corrections_version'] == 1


def test_concurrent_confirm_has_one_winner(profile):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Barrier
    client = Client()
    client.docs['profiles/current'] = profile.model_dump()
    client.barrier = Barrier(2)
    client.barrier_on = lambda path: path == 'profile_corrections/current'
    def confirm(language):
        try:
            return FirestoreProfileCorrections(client).confirm([FactOperation('remove_language', language)], 0).version
        except CorrectionVersionConflict:
            return 'conflict'
    with ThreadPoolExecutor(2) as pool:
        results = list(pool.map(confirm, ['English', 'Spanish']))
    assert sorted(map(str, results)) == ['1', 'conflict']
    assert client.conflicts == 1


def test_forced_build_cannot_reintroduce_base_resume_english(profile):
    from job_agent.application.build_profile import EnsureProfile
    from tests.fakes import FakeResume, FakeRepositories
    client = Client()
    profiles = FirestoreProfileStore(client)
    profiles.save(StoredProfile(profile=profile, built_at=BUILT))
    corrections = FirestoreProfileCorrections(client)
    corrections.confirm([FactOperation('remove_language', 'English')], 0)
    class Inferer:
        def infer(self, resume_text, evidence, preferred_locations, *, corrections):
            assert 'English B2' in resume_text
            assert corrections.operations[0].subject == 'english'
            return profile.model_copy(update={'languages': ['English B2']})
    uc = EnsureProfile(FakeResume('English B2'), FakeRepositories({}), Inferer(), profiles, corrections=corrections)
    assert uc.execute(force=True).languages == []
    assert FirestoreScoringStore(client).load().languages == []


def test_invalid_revoke_writes_nothing(profile):
    client = Client()
    client.docs['profiles/current'] = profile.model_dump()
    with pytest.raises(ValueError):
        FirestoreProfileCorrections(client).confirm([FactOperation('revoke', 'unknown')], 0)
    assert set(client.docs) == {'profiles/current'}


def test_plan_postcommit_failure_preserves_fact_and_can_retry(profile):
    from job_agent.application.manage_search_preferences import ManageSearchPreferences
    from job_agent.domain.policies import SearchBudgets
    from tests.fakes import MemorySearchSettings
    from job_contracts import SearchPreferences
    client = Client()
    profiles = FirestoreProfileStore(client)
    profiles.save(StoredProfile(profile=profile, built_at=BUILT))
    FirestoreProfileCorrections(client).confirm([FactOperation('remove_language', 'English')], 0)
    settings = MemorySearchSettings()
    settings.prefs = SearchPreferences(keywords_include=['Python'], version=1)
    manager = ManageSearchPreferences(store=settings, profiles=profiles, budgets=SearchBudgets())
    original = manager.rebuild_plan
    manager.rebuild_plan = lambda: (_ for _ in ()).throw(RuntimeError('offline'))
    notices = []
    assert manager.after_profile_change(notices.append) is None
    assert 'reintentar' in notices[0]
    assert FirestoreProfileCorrections(client).load().version == 1
    manager.rebuild_plan = original
    assert manager.after_profile_change(notices.append).status == 'rebuilt'


def test_metadata_refresh_cannot_restore_stale_effective_fields(profile):
    client = Client()
    profiles = FirestoreProfileStore(client)
    base = profile.model_copy(update={'languages': ['English B2']})
    stored = StoredProfile(profile=base, built_at=BUILT)
    profiles.save(stored)
    FirestoreProfileCorrections(client).confirm([FactOperation('remove_language', 'English')], 0)
    profiles.save(stored.model_copy(update={'repos_fingerprint': 'checked'}))
    assert profiles.load().profile.languages == []
    assert profiles.load().repos_fingerprint == 'checked'
    assert client.docs['profiles/current']['inferred_profile']['languages'] == ['English B2']
