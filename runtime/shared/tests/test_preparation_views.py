"""Guidance projections preserve governing data and unfamiliar extensions."""
import copy
import hashlib
import json
from pathlib import Path

from shared.preparation_views import model_view, reviewer_view


def preparation(tmp_path):
    snapshot = tmp_path / 'source.json'
    snapshot.write_text('Exact human answer and its conditions.')
    return {
        'preparation_id': 'original', 'engineering_id': 'governing',
        'analysis': {'boundaries': {'compatibility': ['Preserve the exact supported interface.']},
                     'questions': ['An unanswered choice remains open.'],
                     'reviewer_addenda': {'one': 'Review behavior.', 'two': 'Review performance.'},
                     'future_constraint': {'scope': 'Keep this too.'}},
        'graph': {'c-one': {'location': '/boundaries/compatibility/0', 'depends_on': []}},
        'inputs': {'tree': {'module.py': ['100644', 'blob', 'original']}, 'future_policy': 'Keep'},
        'sources': {'choices/0': {'path': str(tmp_path / 'removed-export.json'),
                                  'snapshot': str(snapshot),
                                  'sha256': hashlib.sha256(snapshot.read_bytes()).hexdigest(),
                                  'mode': 'capture'}},
        'reviewers': {name: {'descriptor_version': 1, 'reviewer': name, 'sources': [],
                            'complete': True, 'missing': [], 'effective_prompt': 'Instructions ' * 100,
                            'aspects': ['Spec'], 'execution': None} for name in ('one', 'two')},
        'workflow_dir': str(tmp_path), 'workspace': str(tmp_path / 'checkout'),
        'reviewer_paths': ['descriptor.json'], 'retired_ids': [],
        'recovery_manifest': {'path': 'manifest.json', 'sha256': 'original'},
        'recovery_context': {'path': 'context.json', 'sha256': 'original'},
        'refresh': {'mode': 'initial', 'changed_paths': ['module.py'], 'model_call_count': 1,
                    'phases': [], 'future_constraint': 'Keep this report extension.'},
        'future_constraint': {'human_choice': 'Preserve exactly.'}}


def test_model_view_keeps_complete_analysis_graph_and_readable_evidence(tmp_path):
    original = preparation(tmp_path)
    before = copy.deepcopy(original)
    view = model_view(original)
    for key in ('analysis', 'graph', 'inputs', 'reviewers', 'preparation_id',
                'engineering_id', 'future_constraint'):
        assert view[key] == original[key]
    for key in ('workflow_dir', 'workspace', 'reviewer_paths', 'recovery_manifest', 'recovery_context'):
        assert key not in view
    assert view['refresh'] == {'future_constraint': 'Keep this report extension.'}
    source = view['sources']['choices/0']
    assert hashlib.sha256(Path(source['path']).read_bytes()).hexdigest() == source['sha256']
    assert 'snapshot' not in source
    assert original == before
    assert len(json.dumps(view)) < len(json.dumps(original))
    view['analysis']['questions'].clear()
    assert original == before


def test_unfamiliar_bookkeeping_and_historical_reasoning_remain_visible(tmp_path):
    original = preparation(tmp_path)
    original['workspace'] = {'future_constraint': 'Keep the unknown shape.'}
    original['recovery_manifest']['future_constraint'] = 'A governing extension.'
    original['sources']['choices/0']['future_constraint'] = 'Keep original and snapshot paths.'
    original['refresh']['phases'] = [{'dispositions': [{'reason': 'Historical reasoning remains.'}]}]
    original['refresh']['changed_paths'] = {'future_constraint': 'An unknown shape.'}
    view = model_view(original)
    for key in ('workspace', 'recovery_manifest', 'sources'):
        assert view[key] == original[key]
    assert view['refresh']['phases'] == original['refresh']['phases']
    assert view['refresh']['changed_paths'] == original['refresh']['changed_paths']


def test_reviewer_view_keeps_shared_guidance_and_unfamiliar_descriptor_fields(tmp_path):
    original = preparation(tmp_path)
    original['reviewers']['one']['execution'] = {
        'binding': {'skill': 'example:review', 'host': 'codex', 'handoff_contract': 'git-diff-v2'},
        'identity': 'example:review@codex', 'executor': 'codex', 'plugin_id': 'example@catalog',
        'root': str(tmp_path), 'procedure': str(tmp_path / 'SKILL.md'), 'version': '1.0.0',
        'origin_root': str(tmp_path), 'bundle_digest': 'original'}
    original['reviewers']['two']['sources'] = [{'path': 'procedure.md', 'sha256': 'original',
                                               'future_constraint': 'Retain nested guidance.'}]
    before = copy.deepcopy(original)
    view = reviewer_view(original, 'one')
    assert view['analysis'] == {**original['analysis'], 'reviewer_addenda': {'one': 'Review behavior.'}}
    assert view['reviewers'] == {'two': original['reviewers']['two']}
    assert view['sources'] == model_view(original)['sources']
    assert view['future_constraint'] == original['future_constraint']
    assert 'graph' not in view and 'retired_ids' not in view
    assert len(json.dumps(view)) < len(json.dumps(model_view(original)))
    assert original == before
