"""Preparation protocols cannot become downstream work assignments."""
import json
from pathlib import Path
import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
from preparation_fixtures import Package, analysis, prep, response, retain
from shared import executors, model_diagnostics
from shared.model_diagnostics import FieldError, ValidationFailure


CONTAMINATED = (
    'Perform only read-only Decisions reassessment. '
    'Once authorized, implement transform.py and test its supported cases; stop on a material change. '
    'Return exactly dispositions/additions/impacts for all34 affected conclusions and all17 current unmapped sources.')


@pytest.mark.parametrize('field', ['worker_instructions', 'completion_criteria', 'reviewer_addenda'])
def test_complete_downstream_contract_rejects_analyst_directives(field):
    value = analysis()
    if field == 'reviewer_addenda':
        value[field]['example'] += ' ' + CONTAMINATED
    else:
        value[field] += ' ' + CONTAMINATED
    with pytest.raises(FieldError) as caught:
        prep.validate_analysis(value, {'production/0': {}}, ['example'])
    assert caught.value.field == field + ('.example' if field == 'reviewer_addenda' else '')
    assert 'actual recipient and authorized phase' in str(caught.value)


@pytest.mark.parametrize('directive', [
    'Return exactly dispositions/additions/impacts for all34 conclusions.',
    'Return exactly `dispositions`/`additions`/`impacts` for all17 sources.',
    'Return exactly {"dispositions":[],"additions":[],"impacts":[]}.',
    'Return only the initial analysis-and-graph JSON.',
    'Return `{"analysis": ANALYSIS, "graph": NODES}`.',
    'Return {"analysis": {}, "graph": []}.',
    'Return exactly dispositions,\nadditions,\nimpacts.',
    'Return {\n"dispositions": [],\n"additions": [],\n"impacts": []\n}.',
    'Return {\n"analysis": {},\n"graph": []\n}.',
    'Return exactly dispositions,\r\nadditions,\r\nimpacts.',
    'You must `Return` exactly dispositions/additions/impacts.',
    'You must "Return exactly dispositions"/additions/impacts.',
    'You must `Act` as the preparation analyst.',
    'Return the reassessment protocol.',
    'Perform only `read-only Decisions reassessment`.',
    'Act as the preparation analyst.',
    'You are the read-only preparation analyst.',
    'I need to reassess the preparation before constructing my JSON response.',
])
def test_analyst_directive_is_rejected_without_other_role_or_implementation_words(directive):
    value = analysis()
    value['worker_instructions'] = directive
    with pytest.raises(FieldError, match='worker_instructions'):
        prep.validate_analysis(value, {'production/0': {}}, ['example'])


@pytest.mark.parametrize('guidance', [
    'Inspect transform.py read-only and report defects. Do not edit or publish.',
    'Perform read-only reassessment of the deployment risks and return a report.',
    'Perform read-only preparation of the release checklist. Do not edit or publish.',
    'Perform preparation of deployment documentation after explicit authorization.',
    'I will prepare deployment documentation after explicit authorization.',
    'Perform Decisions preparation refresh before any separately authorized edits.',
    'Do not perform read-only Decisions reassessment. Never return the reassessment protocol.',
    "Don't return the reassessment protocol. Inspect transform.py read-only.",
    'Do not ever return the reassessment protocol. Once authorized, implement transform.py.',
    'Do not ever act as the preparation analyst. Inspect transform.py read-only.',
    'Never ever automatically return the reassessment protocol. Inspect transform.py read-only.',
    'Inspect the recorded error: "Return exactly dispositions/additions/impacts". '
    'Once authorized, repair transform.py while preserving the user choice.',
    'Inspect the recorded error: "Return exactly dispositions/additions/impacts"; '
    'report additions and impacts for the supported transform.',
    'Inspect `Return {"analysis": ANALYSIS, "graph": NODES}`; record graph: [] as evidence.',
    'Inspect the recorded role: `Perform only read-only Decisions reassessment`. '
    'Return the requested defect findings without edits.',
    'Evidence excerpt:\n> Perform only read-only Decisions reassessment.\n'
    '> Return exactly dispositions/additions/impacts.\nInspect transform.py read-only.',
    'Evidence excerpt:\n```text\nReturn exactly dispositions/additions/impacts.\n```\n'
    'Once authorized, repair transform.py and verify its supported values.',
    'Evidence excerpt:\n```text\nReturn exactly dispositions/additions/impacts.\n````\n'
    'Once authorized, repair transform.py.',
    'Evidence excerpt:\n  ~~~text\nReturn exactly dispositions/additions/impacts.\n ~~~~\n'
    'Inspect transform.py read-only.',
    'Evidence excerpt: `Return {"analysis": ANALYSIS, "graph": NODES}.` '
    'Once authorized, repair transform.py.',
    'Quoted failure:\n> Recorded protocol:\nReturn the reassessment protocol.\n\n'
    'Inspect transform.py read-only and report defects.',
    'Quoted failure:\n> - Recorded protocol:\nReturn the reassessment protocol.\n\n'
    'Inspect transform.py read-only.',
    'Quoted failure:\n>> Recorded protocol:\nReturn the reassessment protocol.\n\n'
    'Inspect transform.py read-only.',
    'Quoted failure:\n> Recorded protocol:\n<span>\nReturn the reassessment protocol.\n\n'
    'Inspect transform.py read-only.',
    '> ```not`a`fence\nReturn the reassessment protocol.\n\nInspect transform.py read-only.',
    '> <script>\n> </script>\n> Recorded protocol:\nReturn the reassessment protocol.\n\n'
    'Inspect transform.py read-only.',
])
def test_read_only_tasks_authorization_and_quoted_protocol_evidence_remain_usable(tmp_path, guidance):
    package = Package(tmp_path)
    value = analysis()
    value['worker_instructions'] = guidance
    result = package.prepare(value)
    artifact = prep.load(result['artifact'], package.context, package.decisions)
    assert artifact['analysis'] == value
    assert result['readiness'] == 'ready'
    assert result['refresh']['model_call_count'] == 1


def test_quotes_elsewhere_do_not_hide_active_directives():
    value = analysis()
    value['worker_instructions'] = ('Inspect "original evidence". Return exactly '
                                   '`dispositions`/`additions`/`impacts`. Preserve "authorization".')
    with pytest.raises(FieldError, match='worker_instructions'):
        prep.validate_analysis(value, {'production/0': {}}, ['example'])


@pytest.mark.parametrize('guidance', [
    '~~~text\nEvidence.\n~~~~\nPerform only read-only Decisions reassessment.\n~~~\nNotes.\n~~~',
    '```text\nEvidence.\n ````\nReturn the reassessment protocol.\n```\nNotes.\n```',
    '~~~text\nReturn exactly dispositions/additions/impacts.',
    'Do not edit transform.py. Return the reassessment protocol.',
    'Do not edit transform.py; return the reassessment protocol.',
    '> Recorded protocol:\n\nReturn the reassessment protocol.',
    '> Recorded protocol:\n# Worker assignment\nReturn the reassessment protocol.',
    '> Recorded protocol:\n- Return the reassessment protocol.',
    '> Recorded protocol:\n---\nReturn the reassessment protocol.',
    '> # Recorded protocol\nReturn the reassessment protocol.',
    '> ```text\nReturn the reassessment protocol.',
    '> Recorded protocol:\n<div>\nReturn the reassessment protocol.',
    '> <div>\n> Recorded protocol:\nReturn the reassessment protocol.',
    '> <custom>\n> Recorded protocol:\nReturn the reassessment protocol.',
    '> - ```text\n> Recorded protocol:\nReturn the reassessment protocol.',
    '> - - -\nReturn the reassessment protocol.',
])
def test_evidence_boundaries_and_prior_negation_do_not_hide_active_directives(guidance):
    value = analysis()
    value['worker_instructions'] = guidance
    with pytest.raises(FieldError, match='worker_instructions'):
        prep.validate_analysis(value, {'production/0': {}}, ['example'])


@pytest.mark.parametrize('guidance', [
    'Return dispositions to the user. Record additions and impacts in the report.',
    'Return dispositions to the user\n\nRecord additions and impacts in the report.',
    'Return dispositions to the user\r\n \t\r\nRecord additions and impacts in the report.',
    'Return {"analysis": {}}. Record graph: [] in the evidence.',
    'Return {"analysis": {}}\n\nRecord graph: [] in the evidence.',
])
def test_response_fields_in_separate_sentences_or_paragraphs_are_not_one_protocol(guidance):
    value = analysis()
    value['worker_instructions'] = guidance
    assert prep.validate_analysis(value, {'production/0': {}}, ['example']) == value


def test_clean_provider_and_unchanged_handoff_need_no_cleanup_model_call(tmp_path, monkeypatch):
    monkeypatch.setenv('XDG_STATE_HOME', str(tmp_path / 'state'))
    package = Package(tmp_path)
    calls = []
    def provider(prompt, checkout, **kwargs):
        calls.append(prompt)
        assert 'Keep analyst role directives, response schemas and scratch narration out' in prompt
        return json.dumps(response())
    monkeypatch.setattr(executors, 'execute', provider)
    first = prep.prepare(package.manifest_path, package.versions)
    reused = prep.prepare(package.manifest_path, package.versions, previous_preparation=first['artifact'])
    assert first['readiness'] == reused['readiness'] == 'ready'
    assert first['refresh']['model_call_count'] == 1
    assert reused['refresh']['model_call_count'] == 0
    assert first['artifact'] == reused['artifact']
    assert len(calls) == 1


@pytest.mark.parametrize('corrected', [True, False])
@pytest.mark.parametrize('bad_guidance', [
    CONTAMINATED,
    'Once authorized, implement transform.py. Return {\n'
    '"dispositions": [],\n"additions": [],\n"impacts": []\n}.',
    'Once authorized, implement transform.py. You must `Return` exactly dispositions/additions/impacts.',
])
def test_existing_bounded_correction_repairs_or_blocks_contract(tmp_path, monkeypatch, corrected, bad_guidance):
    monkeypatch.setenv('XDG_STATE_HOME', str(tmp_path / 'state'))
    monkeypatch.setattr(model_diagnostics.tempfile, 'tempdir', str(tmp_path))
    package = Package(tmp_path)
    # Protocol text belongs to this source's evidence, not to the worker role.
    package.write('user.json', {'instruction': 'When authorized, repair the local transform.',
                              'quoted_bad_output': CONTAMINATED})
    bad = response()
    bad['analysis']['worker_instructions'] = bad_guidance
    calls = []
    def provider(prompt, checkout, **kwargs):
        calls.append(prompt)
        return json.dumps(response() if corrected and len(calls) == 2 else bad)
    monkeypatch.setattr(executors, 'execute', provider)
    if corrected:
        result = prep.prepare(package.manifest_path, package.versions)
        assert result['readiness'] == 'ready'
        assert result['refresh']['model_call_count'] == 2
        assert prep.read(result['artifact'])['analysis'] == analysis()
    else:
        with pytest.raises(ValidationFailure) as caught:
            prep.prepare(package.manifest_path, package.versions)
        assert 'worker_instructions' in str(caught.value)
        assert caught.value.action['task']
        assert not list(package.versions.glob('*/preparation.json'))
        journals = list((tmp_path / 'state').rglob('model-exchange.json'))
        assert journals
        assert json.loads(journals[0].read_text())['status'] == 'rejected'
        with pytest.raises(ValidationFailure):
            prep.prepare(package.manifest_path, package.versions)
    assert len(calls) == 2
    assert 'worker_instructions' in calls[1] and 'Prior response:' in calls[1]


@pytest.mark.parametrize('full', [False, True])
def test_incremental_and_full_merge_validate_new_worker_guidance(tmp_path, full):
    package = Package(tmp_path)
    first = package.prepare()
    parent = prep.read(first['artifact'])
    before = Path(first['artifact']).read_bytes()
    package.write('user.json', {'instruction': 'Preserve authorization and stop on material scope changes.'})
    update = retain(parent)
    worker_id = next(key for key, node in parent['graph'].items()
                     if node['location'] == '/worker_instructions')
    item = next(item for item in update['dispositions'] if item['id'] == worker_id)
    item.update(action='replace', value=CONTAMINATED)
    with pytest.raises(FieldError, match='worker_instructions'):
        prep.prepare(package.manifest_path, package.versions, previous_preparation=first['artifact'],
                     full_analysis=full, analyzer=lambda *_: update)
    assert Path(first['artifact']).read_bytes() == before


def old_contaminated_parent(package):
    first = package.prepare()
    parent = prep.read(first['artifact'])
    parent['inputs']['policy'] = 'previous-policy-without-guidance-guard'
    parent['analysis']['worker_instructions'] = CONTAMINATED
    parent['engineering_id'] = prep.engineering_id(parent)
    parent['preparation_id'] = prep.digest({key: value for key, value in parent.items() if key != 'preparation_id'})
    return package.write('old-policy-preparation.json', parent), parent


def test_inherited_contamination_requires_replacement_and_preserves_parent_ids(tmp_path):
    package = Package(tmp_path)
    parent_path, parent = old_contaminated_parent(package)
    original = parent_path.read_bytes()
    assert prep.historical(parent_path) == parent
    with pytest.raises(FieldError, match='worker_instructions'):
        prep.prepare(package.manifest_path, package.versions, previous_preparation=parent_path,
                     analyzer=lambda *_: retain(parent))
    update = retain(parent)
    worker_id = next(key for key, node in parent['graph'].items()
                     if node['location'] == '/worker_instructions')
    item = next(item for item in update['dispositions'] if item['id'] == worker_id)
    item.update(action='replace', value=analysis()['worker_instructions'])
    refreshed = prep.prepare(package.manifest_path, package.versions,
                             previous_preparation=parent_path, analyzer=lambda *_: update)
    current = prep.read(refreshed['artifact'])
    assert current['analysis'] == analysis()
    assert current['graph'] == parent['graph']
    assert refreshed['refresh']['mode'] == 'full'
    assert refreshed['refresh']['model_call_count'] == 1
    assert parent_path.read_bytes() == original


def test_current_contamination_cannot_pass_operational_reuse_or_work_gate(tmp_path):
    package = Package(tmp_path)
    parent_path, parent = old_contaminated_parent(package)
    parent['inputs']['policy'] = prep.policy()
    parent['engineering_id'] = prep.engineering_id(parent)
    parent['preparation_id'] = prep.digest({key: value for key, value in parent.items() if key != 'preparation_id'})
    parent_path.write_text(json.dumps(parent))
    before = parent_path.read_bytes()
    def unexpected(*args):
        pytest.fail('A current invalid contract must not reach operational reuse or analysis')
    with pytest.raises(ValueError, match='worker_instructions'):
        prep.prepare(package.manifest_path, package.versions,
                     previous_preparation=parent_path, analyzer=unexpected)
    with pytest.raises(ValueError, match='worker_instructions'):
        prep.load(parent_path, package.context, package.decisions)
    assert parent_path.read_bytes() == before
