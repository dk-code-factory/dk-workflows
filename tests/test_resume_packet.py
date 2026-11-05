"""Resume packets retain provenance and missing inputs without granting recovery."""
from pathlib import Path
from copy import deepcopy
import pytest
from test_state import case, done


def test_resume_is_read_only_with_freshness_and_explicit_missing_specs(case):
    spec, store = case
    done(store, spec, run='run', cycle='c1')
    other = deepcopy(spec); other['task']['id'] = 'unfinished'
    claim = store.start(other, run='run', cycle='c1')['claim']
    before = store.manifest.read_bytes()
    packet = store.resume([spec], run='run')
    assert store.manifest.read_bytes() == before
    assert packet['run']['cycles'] == ['c1']
    tasks = {t['task']:t for t in packet['tasks']}
    assert tasks['content-review']['current']['reusable']
    assert tasks['unfinished']['missing_references'] == ['governing_spec']
    assert tasks['unfinished']['current'] is None
    assert packet['unfinished'][0]['id'] == claim['id']
    assert packet['limits']['recovery_authorized'] is False
    assert tasks['content-review']['evidence']['result']
    assert tasks['content-review']['attempts'][0]['handle'] is None


def test_resume_changed_inputs_do_not_infer_reuse_or_reset_budgets(case):
    spec, store = case
    done(store, spec, run='run', cycle='c1')
    Path(spec['instructions']['user']).write_text('Changed exact governing input')
    before = store.manifest.read_bytes()
    task = store.resume([spec], run='run')['tasks'][0]
    assert task['current']['reusable'] is False
    assert 'changed_inputs' in task['current']['explanation']['blockers']
    assert store.manifest.read_bytes() == before
    with pytest.raises(ValueError, match='Unknown run'):
        store.resume([spec], run='absent')


def test_resume_damaged_evidence_is_explicit_and_preserves_original_claim(case):
    spec, store = case
    done(store, spec, run='run', cycle='c1')
    path = Path(store.status([spec])['tasks'][0]['result'])
    path.unlink()
    task = store.resume([spec], run='run')['tasks'][0]
    assert task['evidence']['problem'] == 'missing_result_artifact'
    assert task['evidence']['result'] is None
