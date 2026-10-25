"""Incremental preparation through real temporary Git repositories and private artifacts."""
import copy
import hashlib
import itertools
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent))
from preparation_fixtures import Package, analysis, prep, response, retain
from test_preparation import module
from shared import preparation_graph as graph


class IncrementalPreparationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.package = Package(self.temp.name)

    def refresh(self, parent, result=None, **kwargs):
        return prep.prepare(self.package.manifest_path, self.package.root / 'versions',
                            previous_preparation=parent['artifact'],
                            analyzer=lambda *_: result, **kwargs)

    def commit(self, changes, *, amend=False, message='Reassessed fixture'):
        package = self.package
        for name, content in changes.items():
            path = package.repo / name
            if content is None:
                path.unlink()
            else:
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(content)
        prep.git(package.repo, 'add', '-A')
        args = ['commit', '-qm', message]
        if amend:
            args += ['--amend']
        prep.git(package.repo, *args)
        package.head = prep.git(package.repo, 'rev-parse', 'HEAD')
        package.context['head_sha'] = package.head
        package.decisions['head_sha'] = package.head
        package.write('context.json', package.context)
        package.write('decisions.json', package.decisions)
        # Descriptor remains an independent current complete prompt source.
        descriptor = prep.read(package.root / 'reviewer.json')
        descriptor['sources'][0]['sha256'] = prep.file_hash(package.repo / 'transform.py')
        package.write('reviewer.json', descriptor)

    def test_identical_inputs_reuse_artifact_without_model(self):
        first = self.package.prepare()
        with patch.object(prep, 'analyze') as model:
            refreshed = prep.prepare(self.package.manifest_path, self.package.root / 'versions',
                                     previous_preparation=first['artifact'])
        model.assert_not_called()
        self.assertEqual(refreshed['artifact'], first['artifact'])
        self.assertEqual(refreshed['refresh']['mode'], 'unchanged')
        self.assertEqual(refreshed['refresh']['model_call_count'], 0)

    def test_tree_equivalent_amend_preserves_analysis_ids_and_provenance(self):
        first = self.package.prepare()
        old = prep.read(first['artifact'])
        self.commit({}, amend=True)
        result = self.refresh(first)
        fresh = prep.load(result['artifact'], self.package.context, self.package.decisions)
        self.assertEqual(fresh['analysis'], old['analysis'])
        self.assertEqual(fresh['graph'], old['graph'])
        self.assertEqual(fresh['parent']['preparation_id'], first['preparation_id'])
        self.assertEqual(result['refresh']['mode'], 'operational')
        self.assertEqual(result['refresh']['model_call_count'], 0)
        self.assertEqual(prep.historical(first['artifact']), old)
        self.assertNotEqual(fresh['identity']['head_sha'], old['identity']['head_sha'])

    def test_decisions_envelope_outer_fields_require_explicit_reassessment(self):
        package = self.package
        envelope = {'schema_version': 2, 'command': 'context', 'status': 'ok',
                    'data': package.decisions}
        package.write('decisions.json', envelope)
        previous = package.prepare()
        for field in ('external_constraint', 'head_sha'):
            for value in ('first', 'changed', None):
                with self.subTest(field=field, value=value):
                    parent = prep.read(previous['artifact'])
                    if value is None:
                        envelope.pop(field)
                    else:
                        envelope[field] = value
                    package.write('decisions.json', envelope)
                    previous = self.refresh(previous, retain(parent))
                    current = prep.read(previous['artifact'])
                    self.assertEqual(previous['refresh']['model_call_count'], 1)
                    self.assertEqual(previous['refresh']['changed_sources'], ['decisions'])
                    self.assertNotEqual(current['inputs']['sources']['decisions']['governing'],
                                        parent['inputs']['sources']['decisions']['governing'])
                    self.assertEqual(current['analysis'], parent['analysis'])

    def test_decisions_envelope_inner_head_change_preserves_zero_call_refresh(self):
        package = self.package
        envelope = {'schema_version': 2, 'command': 'context', 'status': 'ok',
                    'data': package.decisions}
        package.write('decisions.json', envelope)
        first = package.prepare()
        parent = prep.read(first['artifact'])
        self.commit({}, amend=True)
        package.write('decisions.json', envelope)
        result = self.refresh(first)
        current = prep.read(result['artifact'])
        self.assertEqual(result['refresh']['mode'], 'operational')
        self.assertEqual(result['refresh']['model_call_count'], 0)
        self.assertNotEqual(current['identity']['head_sha'], parent['identity']['head_sha'])
        self.assertEqual(current['inputs']['sources']['decisions']['governing'],
                         parent['inputs']['sources']['decisions']['governing'])
        self.assertEqual(current['analysis'], parent['analysis'])

    def test_typed_operational_evidence_uses_zero_calls_unknown_fields_invalidate(self):
        package = self.package
        history = {'preparation_history_version': 1, 'governing': {'questions': []},
                   'operational': {'cycles': 5, 'publication_receipts': []}}
        package.write('history.json', history)
        package.manifest['evidence']['run_history'] = ['history.json']
        package.write('manifest.json', package.manifest)
        first = package.prepare()
        history['operational']['cycles'] = 6
        package.write('history.json', history)
        result = self.refresh(first)
        self.assertEqual(result['refresh']['model_call_count'], 0)
        self.assertEqual(prep.read(result['artifact'])['analysis'], analysis())
        history['operational']['unknown_choice'] = 'Keep this field governing'
        package.write('history.json', history)
        with self.assertRaises(ValueError):
            self.refresh(result)  # No model response supplied: must not silently reuse.

    def test_unsupported_typed_markers_keep_exact_bytes_and_path_identity(self):
        for field in ('preparation_history_version', 'evidence_version'):
            for marker in (True, 1.0, '1', None, 2):
                with self.subTest(field=field, marker=marker):
                    value = {field: marker, 'source_id': 'same-history', 'governing': {},
                             'operational': {'status': 'pending'}}
                    raw = json.dumps(value).encode()
                    original = graph.semantic('run_history/0', raw)
                    self.assertEqual(original, {'opaque_sha256': hashlib.sha256(raw).hexdigest()})
                    value['operational']['status'] = 'success'
                    self.assertNotEqual(original, graph.semantic('run_history/0', json.dumps(value).encode()))
                    self.assertNotEqual(original, graph.semantic('run_history/0', json.dumps(json.loads(raw), indent=2).encode()))
                    identities = [graph.source_identity('run_history/0', {'path': str(self.package.root / name)},
                                                       self.package.repo, raw) for name in ('first.json', 'moved.json')]
                    self.assertNotEqual(*identities)

    def test_only_integer_typed_markers_allow_zero_call_operational_refresh(self):
        package = self.package
        package.manifest['evidence']['run_history'] = ['history.json']
        package.write('manifest.json', package.manifest)
        for field in ('preparation_history_version', 'evidence_version'):
            for marker in (True, 1.0, 1):
                with self.subTest(field=field, marker=marker):
                    history = {field: marker, 'source_id': 'same-history', 'governing': {},
                               'operational': {'status': 'pending'}}
                    package.write('history.json', history)
                    first = package.prepare()
                    parent = prep.read(first['artifact'])
                    history['operational']['status'] = 'success'
                    package.write('history.json', history)
                    supported = type(marker) is int
                    result = self.refresh(first, None if supported else retain(parent, unmapped=['source:run_history/0']))
                    self.assertEqual(result['refresh']['model_call_count'], 0 if supported else 1)
                    self.assertEqual(result['refresh']['changed_sources'], [] if supported else ['run_history/0'])

    def test_opaque_application_complete_false_is_valid_and_byte_changes_are_assessed(self):
        package = self.package
        self.commit({'settings.json': '{"complete":false}', 'tests/fixture.json': '{"complete":false}'})
        package.manifest['evidence']['production'].append(str(package.repo / 'settings.json'))
        package.manifest['evidence']['tests'] = [str(package.repo / 'tests/fixture.json')]
        package.write('manifest.json', package.manifest)
        previous = package.prepare()
        self.assertEqual(previous['readiness'], 'ready')
        for path, key in (('settings.json', 'production/1'), ('tests/fixture.json', 'tests/0')):
            parent = prep.read(previous['artifact'])
            self.commit({path: '{ "complete": false }\n'})
            result = self.refresh(previous, retain(parent, unmapped=['path:' + path, 'source:' + key]))
            self.assertEqual(result['refresh']['model_call_count'], 1)
            self.assertIn(key, result['refresh']['changed_sources'])
            previous = result

    def test_changed_input_requires_all_affected_and_one_batch(self):
        first = self.package.prepare()
        parent = prep.read(first['artifact'])
        self.package.write('user.json', {'instruction': 'An exact changed instruction'})
        invalid = retain(parent)
        invalid['dispositions'].pop()
        with patch('shared.executors.execute', side_effect=[json.dumps(invalid), json.dumps(retain(parent))]) as model:
            result = prep.prepare(self.package.manifest_path, self.package.root / 'versions', previous_preparation=first['artifact'])
        self.assertEqual(model.call_count, 2)
        self.assertEqual(result['refresh']['model_call_count'], 2)
        with patch('shared.executors.execute', return_value=json.dumps(retain(parent))) as model:
            result = prep.prepare(self.package.manifest_path, self.package.root / 'versions', previous_preparation=first['artifact'])
        self.assertEqual(model.call_count, 1)
        self.assertEqual(result['refresh']['model_call_count'], 1)
        self.assertEqual(prep.read(result['artifact'])['analysis'], parent['analysis'])

    def test_graph_closure_cycles_and_selective_reassessment(self):
        value = response()
        for node in value['graph']:
            node['paths'] = []
            node['sources'] = ['tests/0']
        correctness = '/boundaries/correctness/0'
        performance = '/boundaries/performance/0'
        for node in value['graph']:
            if node['location'] == correctness:
                node['paths'] = [{'path': 'new', 'kind': 'subtree'}]
                node['depends_on'] = [performance]
            elif node['location'] == performance:
                node['depends_on'] = [correctness]
        first = prep.prepare(self.package.manifest_path, self.package.root / 'versions', analyzer=lambda *_: value)
        parent = prep.read(first['artifact'])
        self.commit({'new/caller.py': 'from transform import transform\n'})
        # The prompt descriptor is unchanged: only the added file is substantive.
        paths, changed_sources = graph.changes(parent, graph.inputs(self.package.context, prep.collect(self.package.manifest_path)[3]))
        selected, _ = graph.affected(parent['graph'], paths, changed_sources)
        self.assertEqual(len(selected), 5)  # Two cyclic boundaries + three mandatory guidance nodes.
        result = self.refresh(first, retain(parent, selected, ['path:new/caller.py']))
        self.assertEqual(result['refresh']['model_call_count'], 1)
        self.assertEqual(len(result['refresh']['reused_conclusions']), len(parent['graph']))
        self.assertIn('path:new/caller.py', result['refresh']['unmapped_changes'])

    def test_unmapped_change_cannot_be_ignored_and_can_expand_impact(self):
        first = self.package.prepare()
        parent = prep.read(first['artifact'])
        self.commit({'unmapped.md': 'A changed documented assumption\n'})
        with self.assertRaisesRegex(ValueError, 'every unmapped'):
            self.refresh(first, retain(parent))
        valid = retain(parent, unmapped=['path:unmapped.md'])
        valid['impacts'][0]['affected'] = list(parent['graph'])
        result = self.refresh(first, valid)
        self.assertEqual(result['refresh']['model_call_count'], 1)

    def test_explicit_escalation_allows_only_one_full_fallback(self):
        first = self.package.prepare()
        parent = prep.read(first['artifact'])
        self.package.write('user.json', {'instruction': 'Cross-cutting change'})
        escalation = {'full_reassessment': True, 'reason': 'Cannot bound the scope of this interaction'}
        with patch('shared.executors.execute', side_effect=[json.dumps(escalation), json.dumps(retain(parent))]) as model:
            result = prep.prepare(self.package.manifest_path, self.package.root / 'versions', previous_preparation=first['artifact'])
        self.assertEqual(model.call_count, 2)
        self.assertEqual(result['refresh']['mode'], 'full_fallback')
        self.assertEqual(result['refresh']['model_call_count'], 2)
        with patch('shared.executors.execute', return_value=json.dumps(escalation)) as model:
            with self.assertRaisesRegex(ValueError, 'cannot escalate again'):
                prep.prepare(self.package.manifest_path, self.package.root / 'versions', previous_preparation=first['artifact'])
        self.assertEqual(model.call_count, 3)  # Initial escalation, full phase and its one correction.

    def test_new_conclusions_get_fresh_ids_deleted_ids_stay_retired(self):
        first = self.package.prepare()
        parent = prep.read(first['artifact'])
        self.package.write('user.json', {'instruction': 'Add a concrete correctness boundary'})
        change = retain(parent)
        value = copy.deepcopy(analysis()['boundaries']['correctness'][0])
        value['text'] = 'New explicitly inspected correctness boundary'
        change['additions'] = [{'location': '/boundaries/correctness/-', 'value': value,
                                'watches': {'paths': [{'path': 'optional', 'kind': 'subtree'}],
                                            'sources': ['production/0'], 'depends_on': [], 'decisions': []},
                                'reason': 'New governing source'}]
        second = self.refresh(first, change)
        updated = prep.read(second['artifact'])
        added = next(iter(updated['graph'].keys() - parent['graph'].keys()))
        self.assertEqual(updated['graph'][added]['location'], '/boundaries/correctness/1')
        self.package.write('user.json', {'instruction': 'Explicitly supersede the added routine boundary'})
        change = retain(updated)
        for item in change['dispositions']:
            if item['id'] == added:
                item['action'] = 'remove'
            elif added in updated['graph'][item['id']]['depends_on']:
                item['watches'] = {k: copy.deepcopy(v) for k, v in updated['graph'][item['id']].items() if k != 'location'}
                item['watches']['depends_on'].remove(added)
        third = self.refresh(second, change)
        final = prep.read(third['artifact'])
        self.assertEqual(final['retired_ids'], [added])
        self.assertEqual(final['analysis'], analysis())

    def dependent_addition_batch(self, parent):
        change = retain(parent)
        watches = {'paths': [{'path': 'new-boundary', 'kind': 'subtree'}],
                   'sources': ['production/0'], 'depends_on': ['addition:1'], 'decisions': []}
        change['additions'] = [
            {'location': '/boundaries/correctness/-',
             'value': {**parent['analysis']['boundaries']['correctness'][0], 'text': 'New governing boundary'},
             'watches': watches, 'reason': 'New inspected boundary'},
            {'location': '/test_strategy/-',
             'value': {**parent['analysis']['test_strategy'][0], 'behavior': 'New boundary regression'},
             'watches': {**watches, 'paths': [{'path': 'new-tests', 'kind': 'subtree'}],
                         'depends_on': ['addition:0']}, 'reason': 'New dependent regression'}]
        for item in change['dispositions']:
            node = parent['graph'][item['id']]
            if node['location'] == '/placement/tests':
                item['watches'] = {k: copy.deepcopy(v) for k, v in node.items() if k != 'location'}
                item['watches']['depends_on'].append('addition:1')
        return change

    def test_same_batch_additions_resolve_forward_cycles_and_existing_dependents(self):
        for full in (False, True):
            with self.subTest(full=full):
                first = self.package.prepare()
                parent = prep.read(first['artifact'])
                self.package.write('user.json', {'instruction': f'Add the inspected boundary and regression: {full}'})
                result = self.refresh(first, self.dependent_addition_batch(parent), full_analysis=full)
                self.assertEqual(result['refresh']['model_call_count'], 1)
                self.assertEqual(result['refresh']['mode'], 'full' if full else 'incremental')
                current = prep.historical(result['artifact'])
                ids = {node['location']: key for key, node in current['graph'].items()}
                boundary, test = ids['/boundaries/correctness/1'], ids['/test_strategy/1']
                existing = ids['/placement/tests']
                self.assertEqual(current['graph'][boundary]['depends_on'], [test])
                self.assertEqual(current['graph'][test]['depends_on'], [boundary])
                self.assertIn(test, current['graph'][existing]['depends_on'])
                for key, node in parent['graph'].items():
                    self.assertEqual(current['graph'][key]['location'], node['location'])
                    self.assertEqual(graph.conclusions(current['analysis'])[node['location']],
                                     graph.conclusions(parent['analysis'])[node['location']])
                self.assertEqual(current['analysis']['boundaries']['correctness'][1]['text'], 'New governing boundary')
                path = f'new-boundary/{full}.py'
                self.commit({path: 'changed = True\n'})
                selected, _ = graph.affected(current['graph'], [path], [])
                self.assertTrue({boundary, test, existing}.issubset(selected))
                refreshed = self.refresh(result, retain(current, selected, ['path:' + path]))
                self.assertEqual(refreshed['refresh']['model_call_count'], 1)
                self.assertEqual(prep.read(refreshed['artifact'])['analysis'], current['analysis'])
                self.assertEqual(prep.read(refreshed['artifact'])['graph'], current['graph'])

    def test_batch_addition_references_reject_malformed_dangling_and_colliding_ids(self):
        first = self.package.prepare()
        parent = prep.read(first['artifact'])
        for reference in ('addition:-1', 'addition:01', 'addition:2', 'addition:1.0', 'addition:', 'c-missing', 0, {}):
            change = self.dependent_addition_batch(parent)
            change['additions'][0]['watches']['depends_on'] = [reference]
            with self.subTest(reference=reference), self.assertRaises(ValueError):
                graph.merge(parent, change, set(parent['graph']), [], parent['sources'], self.package.decisions)
        change = self.dependent_addition_batch(parent)
        change['additions'][0]['watches']['depends_on'] = ['addition:0', 'addition:0']
        with self.assertRaisesRegex(ValueError, 'references'):
            graph.merge(parent, change, set(parent['graph']), [], parent['sources'], self.package.decisions)
        for collision in ('existing', 'retired', 'new'):
            invalid = copy.deepcopy(parent)
            allocated = graph.uuid.UUID(hex=next(iter(parent['graph']))[2:]) if collision == 'existing' else graph.uuid.UUID(int=0)
            other = graph.uuid.UUID(int=1)
            if collision == 'retired':
                invalid['retired_ids'].append('c-' + allocated.hex)
            with self.subTest(collision=collision), patch.object(graph, 'uuid') as allocator:
                allocator.uuid4.side_effect = [allocated, allocated if collision == 'new' else other]
                with self.assertRaisesRegex(ValueError, 'collision'):
                    graph.merge(invalid, self.dependent_addition_batch(parent), set(parent['graph']), [],
                                parent['sources'], self.package.decisions)

    def test_persisted_graph_ids_cannot_reorder_list_conclusions(self):
        value = analysis()
        question = module('ordered_questions', 'dk-pr-decisions/tests/test_preparation.py').PreparationTests.question(self)
        question.update(status='resolved', resolution='Exact human answer retained in user_instructions/0')
        lists = {'/boundaries/correctness': (value['boundaries']['correctness'], 'text'),
                 '/test_strategy': (value['test_strategy'], 'behavior'),
                 '/questions': (value['questions'], 'id')}
        for location, (target, field) in lists.items():
            template = question if location == '/questions' else target[0]
            target[:] = [{**copy.deepcopy(template), field: f'Original {n}'} for n in range(12)]
        count = len(graph.conclusions(value))
        # Persistence sorts IDs. Assign them in reverse location order to make the
        # mismatch deterministic, including list indices 10/11 versus 2.
        identifiers = [graph.uuid.UUID(int=n) for n in range(count, 0, -1)]
        with patch.object(graph, 'uuid') as allocator:
            allocator.uuid4.side_effect = itertools.cycle(identifiers)
            first = self.package.prepare(value)
        parent = prep.read(first['artifact'])
        ids = {node['location']: key for key, node in parent['graph'].items()}
        self.assertLess(ids['/boundaries/correctness/1'], ids['/boundaries/correctness/0'])
        self.package.write('user.json', {'instruction': 'Recheck existing conclusions without changing them'})
        retained = prep.read(self.refresh(first, retain(parent))['artifact'])
        self.assertEqual(retained['analysis'], value)
        self.assertEqual(retained['graph'], parent['graph'])

        change = retain(parent)
        removed = {ids[location + '/1'] for location in lists}
        expected = copy.deepcopy(value)
        for item in change['dispositions']:
            location = parent['graph'][item['id']]['location']
            if item['id'] in removed:
                item['action'] = 'remove'
            elif location.rsplit('/', 1)[0] in lists and location.endswith('/10'):
                field = lists[location.rsplit('/', 1)[0]][1]
                item.update(action='replace', value={**graph.conclusions(value)[location], field: 'Replacement'})
            dependencies = parent['graph'][item['id']]['depends_on']
            if removed & set(dependencies):
                item['watches'] = {k: copy.deepcopy(v) for k, v in parent['graph'][item['id']].items() if k != 'location'}
                item['watches']['depends_on'] = [key for key in dependencies if key not in removed]
        for location, (original, field) in lists.items():
            additions = [{**copy.deepcopy(original[0]), field: f'Added {n}'} for n in (1, 2)]
            for added in additions:
                change['additions'].append({'location': location + '/-', 'value': added,
                    'watches': {'paths': [], 'sources': ['production/0'], 'depends_on': [], 'decisions': []},
                    'reason': 'Explicitly added after the surviving conclusions'})
            updated = [copy.deepcopy(v) for n, v in enumerate(original) if n != 1]
            updated[9][field] = 'Replacement'
            updated.extend(additions)
            if location.startswith('/boundaries/'):
                expected['boundaries']['correctness'] = updated
            else:
                expected[location[1:]] = updated
        self.package.write('user.json', {'instruction': 'Replace, remove and append the selected conclusions'})
        final = prep.read(self.refresh(first, change)['artifact'])
        self.assertEqual(final['analysis'], expected)
        self.assertEqual(set(final['retired_ids']), removed)
        for location in lists:
            for old, new in ((0, 0), (2, 1), (10, 9), (11, 10)):
                self.assertEqual(final['graph'][ids[f'{location}/{old}']]['location'], f'{location}/{new}')
        self.assertEqual(set(final['graph']) & set(parent['graph']), set(parent['graph']) - removed)

    def test_parent_corruption_or_wrong_pr_blocks_without_model(self):
        first = self.package.prepare()
        path = Path(first['artifact'])
        original = path.read_text()
        path.chmod(0o600)
        for bad in ('not JSON', json.dumps({**prep.read(path), 'graph': {}})):
            path.write_text(bad)
            with patch.object(prep, 'analyze') as model, self.assertRaisesRegex(ValueError, 'invalid selected parent'):
                self.refresh(first)
            model.assert_not_called()
        path.write_text(original)
        self.package.context['url'] = 'https://github.com/example/fixture/pull/2'
        self.package.decisions['pr_url'] = self.package.context['url']
        self.package.write('context.json', self.package.context)
        self.package.write('decisions.json', self.package.decisions)
        with self.assertRaisesRegex(ValueError, 'different PR'):
            self.refresh(first)

    def test_manifest_mode_change_between_parse_and_hash_cannot_publish(self):
        package = self.package
        original_hash = prep.file_hash
        for before, after in (('standalone', 'iteration'), ('iteration', 'standalone')):
            with self.subTest(before=before, after=after):
                package.manifest['mode'] = before
                package.write('manifest.json', package.manifest)
                mutated = False
                def mutate_before_hash(path):
                    nonlocal mutated
                    if Path(path).resolve() == package.manifest_path and not mutated:
                        mutated = True
                        package.manifest['mode'] = after
                        package.write('manifest.json', package.manifest)
                    return original_hash(path)
                with patch.object(prep, 'file_hash', side_effect=mutate_before_hash):
                    with self.assertRaisesRegex(ValueError, 'changed during analysis'):
                        package.prepare()
                self.assertTrue(mutated)
                self.assertEqual(list((package.root / 'versions').glob('*/preparation.json')), [])

    def test_reviewer_descriptor_change_between_parse_and_hash_cannot_publish(self):
        package = self.package
        descriptor_path = package.root / 'reviewer.json'
        descriptor = prep.read(descriptor_path)
        original_hash = prep.file_hash
        for field, value in (('effective_prompt', 'Inspect the changed review instructions.'),
                             ('aspects', ['Standards'])):
            with self.subTest(field=field):
                package.write('reviewer.json', descriptor)
                mutated = False
                def mutate_before_hash(path):
                    nonlocal mutated
                    if Path(path).resolve() == descriptor_path and not mutated:
                        mutated = True
                        package.write('reviewer.json', {**descriptor, field: value})
                    return original_hash(path)
                with patch.object(prep, 'file_hash', side_effect=mutate_before_hash):
                    with self.assertRaisesRegex(ValueError, 'changed during analysis'):
                        package.prepare()
                self.assertTrue(mutated)
                self.assertEqual(list((package.root / 'versions').glob('*/preparation.json')), [])

    def test_stale_inputs_interruptions_and_concurrent_activation(self):
        package = self.package
        package.manifest['mode'] = 'iteration'
        package.write('manifest.json', package.manifest)
        first = package.prepare()
        parent = prep.read(first['artifact'])
        state = module('incremental_state', 'dk-pr-iter/scripts/run_state.py')
        record = {}
        state.start_run(record)
        state.attach_preparation(record, first['artifact'], package.context['url'], package.head)
        package.write('user.json', {'instruction': 'Changed before reassessment'})
        def stale(*_):
            package.write('user.json', {'instruction': 'Changed during reassessment'})
            return retain(parent)
        with self.assertRaisesRegex(ValueError, 'source changed during analysis'):
            prep.prepare(package.manifest_path, package.root / 'versions', previous_preparation=first['artifact'], analyzer=stale)
        with self.assertRaises(KeyboardInterrupt):
            prep.prepare(package.manifest_path, package.root / 'versions', previous_preparation=first['artifact'],
                         analyzer=lambda *_: (_ for _ in ()).throw(KeyboardInterrupt()))
        self.assertEqual(list((package.root / 'versions').glob('*/preparation.json')), [Path(first['artifact'])])
        second = self.refresh(first, retain(parent))
        other = self.refresh(first, retain(parent))
        state.attach_preparation(record, second['artifact'], package.context['url'], package.head)
        with self.assertRaisesRegex(ValueError, 'active preparation changed'):
            state.attach_preparation(record, other['artifact'], package.context['url'], package.head)
        self.assertEqual(record['preparation']['id'], second['preparation_id'])

    def test_fresh_run_can_initialize_with_child_but_rejects_competing_activation(self):
        package = self.package
        package.manifest['mode'] = 'iteration'
        package.write('manifest.json', package.manifest)
        first = package.prepare()
        parent = prep.read(first['artifact'])
        state = module('fresh_lineage_state', 'dk-pr-iter/scripts/run_state.py')
        record = {}
        old_run = state.start_run(record)['id']
        state.attach_preparation(record, first['artifact'], package.context['url'], package.head)
        state.next_cycle(record, package.head)
        state.finish_run(record, 'completed', 'Previous request completed')
        state.start_run(record)
        package.write('user.json', {'instruction': 'A fresh request refreshes the previous preparation'})
        child = self.refresh(first, retain(parent))
        sibling = self.refresh(first, retain(parent))
        with self.assertRaisesRegex(ValueError, 'expected parent differs'):
            state.attach_preparation(record, child['artifact'], package.context['url'], package.head,
                                     expected_parent='wrong-parent')
        state.attach_preparation(record, child['artifact'], package.context['url'], package.head,
                                 expected_parent=first['preparation_id'])
        history_size = len(record['preparations'])
        state.attach_preparation(record, child['artifact'], package.context['url'], package.head,
                                 expected_parent=first['preparation_id'])
        self.assertEqual(len(record['preparations']), history_size)
        self.assertEqual(record['run_history'][0]['iteration_run']['id'], old_run)
        self.assertEqual(len(record['run_history'][0]['cycles']), 1)
        state.next_cycle(record, package.head)
        self.assertEqual(len(record['cycles']), 1)
        with self.assertRaisesRegex(ValueError, 'active preparation changed'):
            state.attach_preparation(record, sibling['artifact'], package.context['url'], package.head)
        record.pop('preparation')
        with self.assertRaisesRegex(ValueError, 'active preparation changed'):
            state.attach_preparation(record, sibling['artifact'], package.context['url'], package.head)

    def test_parentless_retry_cannot_roll_back_active_pending_child(self):
        package = self.package
        package.manifest['mode'] = 'iteration'
        package.write('manifest.json', package.manifest)
        first, competing_root = package.prepare(), package.prepare()
        parent = prep.read(first['artifact'])
        state = module('root_retry_state', 'dk-pr-iter/scripts/run_state.py')
        record = {}
        state.start_run(record)
        state.attach_preparation(record, first['artifact'], package.context['url'], package.head)
        state.attach_preparation(record, first['artifact'], package.context['url'], package.head)
        self.assertEqual(len(record['preparations']), 1)
        state.next_cycle(record, package.head)
        change = retain(parent)
        key = next(k for k, node in parent['graph'].items() if node['location'] == '/boundaries/correctness/0')
        for item in change['dispositions']:
            if item['id'] == key:
                item.update(action='replace', value={**parent['analysis']['boundaries']['correctness'][0],
                            'agreement': 'proposal', 'material': True, 'text': 'Human confirmation required'})
        child = self.refresh(first, change, full_analysis=True)
        sibling = self.refresh(first, retain(parent), full_analysis=True)
        state.attach_preparation(record, child['artifact'], package.context['url'], package.head)
        state.attach_preparation(record, child['artifact'], package.context['url'], package.head)
        self.assertEqual(len(record['preparations']), 2)
        self.assertEqual(prep.load(first['artifact'], package.context, package.decisions)['readiness'], 'ready')
        for candidate in (first, competing_root, sibling):
            for expected in (None, first['preparation_id'], child['preparation_id']):
                with self.subTest(candidate=candidate['preparation_id'], expected=expected):
                    with self.assertRaises(ValueError):
                        state.attach_preparation(record, candidate['artifact'], package.context['url'], package.head,
                                                 expected_parent=expected)
                    self.assertEqual(record['preparation']['id'], child['preparation_id'])
        self.assertEqual(state.work_gate(record, package.head, require_ready=False)['readiness'], 'needs_decision')
        with self.assertRaisesRegex(ValueError, 'needs_decision'):
            state.next_cycle(record, package.head)
        self.assertEqual(len(record['cycles']), 1)
        record.pop('preparation')
        with self.assertRaisesRegex(ValueError, 'active preparation changed'):
            state.attach_preparation(record, first['artifact'], package.context['url'], package.head)

    def test_v1_migration_uses_fresh_analysis_and_separate_storage(self):
        package = self.package
        manifest, context, decisions, sources, reviewers, _ = prep.collect(package.manifest_path)
        # Original v1 sources have only path/hash; retained copies live at evidence/KEY.
        old = {'preparation_version': 1, 'mode': manifest['mode'],
               'identity': {'pr_url': context['url'], 'base_sha': context['base_sha'],
                            'head_sha': context['head_sha'], 'body_hash': decisions['body_hash'],
                            'decisions_hash': prep.digest(decisions)},
               'sources': sources, 'reviewers': reviewers, 'analysis': analysis(), 'readiness': 'ready'}
        old['preparation_id'] = prep.digest(old)
        legacy_dir = package.root / 'legacy-v1' / old['preparation_id'][:20]
        for key, source in sources.items():
            self.assertEqual(set(source), {'path', 'sha256'})
            target = legacy_dir / 'evidence' / key
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(Path(source['path']).read_bytes())
        old_path = legacy_dir / 'preparation.json'
        old_path.write_text(json.dumps(old))
        before = {path.relative_to(legacy_dir): path.read_bytes() for path in legacy_dir.rglob('*') if path.is_file()}
        package.write('user.json', {'instruction': 'Reassess the original preparation with current evidence'})
        fresh_analysis = analysis()
        fresh_analysis['worker_instructions'] += ' Inspect the current instruction evidence.'
        prompts = []
        def model(prompt, checkout):
            prompts.append(prompt)
            return response(fresh_analysis)
        result = prep.prepare(package.manifest_path, package.root / 'versions',
                              previous_preparation=old_path, analyzer=model)
        migrated = prep.read(result['artifact'])
        self.assertEqual(len(prompts), 1)
        self.assertIn('MIGRATION: inspect original v1 preparation at ' + str(old_path), prompts[0])
        self.assertIn('"preparation_version": 1', prompts[0])
        self.assertEqual(result['refresh']['mode'], 'migration')
        self.assertEqual(result['refresh']['model_call_count'], 1)
        self.assertEqual(migrated['preparation_version'], 2)
        self.assertEqual(migrated['analysis'], fresh_analysis)
        self.assertTrue(migrated['parent']['migration'])
        self.assertEqual(migrated['parent']['preparation_id'], old['preparation_id'])
        self.assertEqual(migrated['parent']['path'], str(old_path))
        self.assertEqual(Path(result['artifact']).parent.parent, package.root / 'versions')
        self.assertEqual(prep.historical(old_path), old)
        self.assertEqual({path.relative_to(legacy_dir): path.read_bytes()
                          for path in legacy_dir.rglob('*') if path.is_file()}, before)
        snapshot = legacy_dir / 'evidence/production/0'
        original = snapshot.read_bytes()
        def stale_migration(*_):
            snapshot.unlink()
            return response(fresh_analysis)
        try:
            with self.assertRaisesRegex(ValueError, 'invalid selected parent'):
                prep.prepare(package.manifest_path, package.root / 'versions',
                             previous_preparation=old_path, analyzer=stale_migration)
        finally:
            snapshot.write_bytes(original)
        self.assertEqual(list((package.root / 'versions').glob('*/preparation.json')), [Path(result['artifact'])])

    def test_graph_rejects_invalid_watches_dangling_references_and_missing_coverage(self):
        for mutate in (lambda v: v['graph'].pop(),
                       lambda v: v['graph'][0]['paths'].append({'path': '../outside', 'kind': 'subtree'}),
                       lambda v: v['graph'][0]['paths'].append({'path': '*.py', 'kind': 'file'}),
                       lambda v: v['graph'][0]['paths'].append({'path': 'invalid\x00name.py', 'kind': 'file'}),
                       lambda v: v['graph'][0]['paths'].append({'path': 'invalid\x00directory', 'kind': 'subtree'}),
                       lambda v: v['graph'][0]['paths'].append({'path': 'invalid\ud800.py', 'kind': 'file'}),
                       lambda v: v['graph'][0]['paths'].append({'path': 'invalid\udc00', 'kind': 'subtree'}),
                       lambda v: v['graph'][0]['paths'].append({'path': '\udcc3\udca9.py', 'kind': 'file'}),
                       lambda v: v['graph'][0]['depends_on'].append('/does-not-exist')):
            value = response()
            mutate(value)
            with self.subTest(value=value), self.assertRaises(ValueError):
                prep.prepare(self.package.manifest_path, self.package.root / 'versions', analyzer=lambda *_: value)

    def test_fifth_review_then_tree_equal_cleanup_completes_without_preparation_model(self):
        from shared import executors, review_sessions, review_coverage, review_reconciliation
        from shared.private_state import write
        package = self.package
        self.commit({'README.md': 'Reviewed transform change\n'})
        package.manifest['mode'] = 'iteration'
        package.write('manifest.json', package.manifest)
        first = package.prepare()
        prepared = prep.read(first['artifact'])
        state = module('fifth_cycle_state', 'dk-pr-iter/scripts/run_state.py')
        completion = module('fifth_cycle_completion', 'dk-pr-iter/scripts/completion.py')
        record, record_path = {}, package.root / 'run.json'
        state.start_run(record)
        state.attach_preparation(record, first['artifact'], package.context['url'], package.head)
        for cycle in range(1, 6):
            state.next_cycle(record, package.head)
            binding = state.review_session(record, package.head, record_path)
            write(record_path, record)
            session = review_sessions.Session(package.context, prepared, ['example'], path=binding['session_file'])
            item = session.finish({'complete': True, 'findings': [], 'executed_reviewers': ['example'],
                                   'reused_reviewers': [], 'skipped_reviewers': []})
            state.register_review(record, {'review_artifact': item}, package.head)
        write(record_path, record)
        reviewed_head = package.head
        reviewed_tree = prep.git(package.repo, 'rev-parse', 'HEAD^{tree}')
        original_review = Path(item['path']).read_bytes()
        original_cycles = copy.deepcopy(record['cycles'])
        self.commit({}, amend=True, message='Regrouped fixture')
        self.assertNotEqual(package.head, reviewed_head)
        self.assertEqual(prep.git(package.repo, 'rev-parse', 'HEAD^{tree}'), reviewed_tree)
        with patch.object(prep, 'analyze') as model:
            fresh = prep.prepare(package.manifest_path, package.root / 'versions', previous_preparation=first['artifact'])
        model.assert_not_called()
        self.assertEqual(fresh['refresh']['model_call_count'], 0)
        state.attach_preparation(record, fresh['artifact'], package.context['url'], package.head,
                                 expected_parent=first['preparation_id'])
        write(record_path, record)
        prepared = prep.read(fresh['artifact'])
        with self.assertRaisesRegex(ValueError, 'scope lacks review coverage'):
            state.verify_review(record_path, prepared)
        self.assertEqual(len(record['cycles']), 5)
        with self.assertRaisesRegex(ValueError, 'five review cycles'):
            state.next_cycle(record, package.head)
        source = package.repo / 'transform.py'
        check = {'satisfied': True, 'rationale': 'Inspected current transform and validation evidence',
                 'evidence': [{'path': str(source), 'sha256': prep.file_hash(source)}]}
        verification = package.write('verification.json', {'verification_version': 1,
            'preparation_id': fresh['preparation_id'], 'pr_url': package.context['url'], 'head_sha': package.head,
            'checks': {name: check for name in ('behavior', 'placement', 'test_sufficiency', 'compatibility', 'complexity')}})
        tree = prep.git(package.repo, 'rev-parse', 'HEAD^{tree}')
        assessment = {'schema_version': 1, 'pr_url': package.context['url'], 'head_repo': 'example/fixture',
            'target_branch': 'main', 'reviewed_head': reviewed_head, 'reviewed_tree': tree,
            'target_tip': package.context['base_sha'], 'final_head': package.head,
            'outcome': 'published', 'commit_count': 1, 'reasons': ['One coherent transform change']}
        live = {'pr_url': package.context['url'], 'head_repo': 'example/fixture', 'target_branch': 'main',
            'target_tip': package.context['base_sha'], 'final_head': package.head, 'final_tree': tree, 'commit_count': 1}
        assessment_path = package.write('series-assessment.json', assessment)
        comparisons = []
        def compare(prompt, checkout, *, attempt=0):
            comparisons.append(attempt)
            binding = json.JSONDecoder().raw_decode(prompt[prompt.index('{"binding":'):])[0]['binding']
            self.assertEqual(binding['original_preparation_id'], first['preparation_id'])
            self.assertEqual(binding['current_preparation_id'], fresh['preparation_id'])
            self.assertEqual(binding['review_artifact'], item)
            def verdict(key=None):
                refs = ['original/analysis', 'current/analysis']
                refs += [side + '/' + key for side in ('original', 'current')
                         if key is not None and side + '/' + key in binding['evidence']]
                return {'verdict': 'equivalent', 'rationale': 'Only the tree-equivalent cleanup identity changed',
                        'evidence': refs}
            return json.dumps({'equivalent': True, 'rationale': 'The retained engineering obligations are unchanged',
                'fields': {key: verdict() for key in binding['fields']},
                'sources': {key: verdict(key) for key in binding['changed_sources']}})
        with patch.object(executors, 'execute', side_effect=compare), \
                patch.object(review_reconciliation.series, 'live_snapshot', return_value=live), \
                patch.object(prep, 'analyze') as model:
            receipt = review_reconciliation.reconcile(record_path, package.head, assessment_path,
                                                       state.reconciliation_api())
        model.assert_not_called()
        self.assertEqual(comparisons, [0])
        self.assertEqual(receipt['status'], 'reconciled')
        self.assertEqual(receipt['executed_reviewers'], [])
        self.assertEqual(receipt['retained_reviewers'], ['example'])
        verified = state.verify_review(record_path, prepared, assessment=assessment)
        self.assertEqual(verified['head'], reviewed_head)
        self.assertEqual(verified['preparation_id'], first['preparation_id'])
        self.assertEqual(verified['retained_coverage']['executed_reviewers'], [])
        self.assertEqual(Path(item['path']).read_bytes(), original_review)
        self.assertEqual(state.read(record_path, {})['cycles'], original_cycles)
        args = (reviewed_head, package.head, package.head, True, False, 'success')
        self.assertEqual(completion.completion_gate(*args, preparation=fresh['artifact'], run_record=record_path,
            pr_url=package.context['url'], body='', verification=verification, assessment=assessment, series_snapshot=live), 'clean')
        # Preparation graph/report changes do not enter strict review scope.
        equivalent = copy.deepcopy(prepared)
        equivalent['refresh']['unrelated_report_field'] = True
        self.assertEqual(review_coverage.scope(equivalent), review_coverage.scope(prepared))
        for changed in ('instructions', 'scope', 'tree'):
            invalid = copy.deepcopy(prepared)
            if changed == 'instructions':
                invalid['analysis']['worker_instructions'] += ' New obligation.'
            elif changed == 'scope':
                invalid['sources']['rules/0']['sha256'] = 'changed'
            else:
                self.commit({'new.py': 'a = 1\n'})
                invalid['identity']['head_sha'] = package.head
            guard = ('preparation source identity is stale' if changed == 'tree' else
                     'reconciliation does not cover this review and preparation')
            with self.subTest(changed=changed), self.assertRaisesRegex(ValueError, guard):
                state.verify_review(record_path, invalid)

    def test_source_relocation_and_order_preserve_citations_only_for_known_identity(self):
        package = self.package
        for name in ('one', 'two'):
            package.write(name + '.json', {'evidence_version': 1, 'source_id': name, 'governing': {'text': name}})
        package.manifest['evidence']['tests'] = ['one.json', 'two.json']
        package.write('manifest.json', package.manifest)
        first = package.prepare()
        (package.root / 'one.json').rename(package.root / 'relocated.json')
        package.manifest['evidence']['tests'] = ['two.json', 'relocated.json']
        package.write('manifest.json', package.manifest)
        result = self.refresh(first)
        fresh = prep.read(result['artifact'])
        self.assertEqual(result['refresh']['model_call_count'], 0)
        self.assertEqual(fresh['sources']['tests/0']['path'], str(package.root / 'relocated.json'))
        self.assertEqual(fresh['analysis'], analysis())

    def test_source_removal_and_new_absence_evidence_require_impact(self):
        package = self.package
        first = package.prepare()
        parent = prep.read(first['artifact'])
        package.write('tests.json', {'evidence_version': 1, 'source_id': 'tests', 'governing': {'found': ['new_test.py']}})
        package.manifest['evidence']['tests'] = ['tests.json']
        package.write('manifest.json', package.manifest)
        # New identity gets a fresh source slot; deletion cannot alias old citations.
        current = graph.inputs(package.context, prep.collect(package.manifest_path)[3])
        sources, current = graph.rebind(prep.collect(package.manifest_path)[3], current, parent)
        paths, changed_sources = graph.changes(parent, current)
        self.assertIn('tests/0', changed_sources)
        self.assertIn('tests/1', changed_sources)
        selected, unmapped = graph.affected(parent['graph'], paths, changed_sources)
        unmapped = sorted(set(unmapped) | {'source:tests/1'})
        result = self.refresh(first, retain(parent, selected, unmapped))
        self.assertEqual(result['refresh']['model_call_count'], 1)

    def test_base_only_change_requires_full_dispositions_at_unchanged_head_and_tree(self):
        self.commit({'README.md': 'Base advancement fixture\n'})
        first = self.package.prepare()
        parent = prep.read(first['artifact'])
        self.package.context['base_sha'] = self.package.head
        self.package.write('context.json', self.package.context)
        invalid = retain(parent, unmapped=['source:context'])
        invalid['dispositions'].pop(0)
        with self.assertRaisesRegex(ValueError, 'every affected conclusion'):
            self.refresh(first, invalid)
        result = self.refresh(first, retain(parent, unmapped=['source:context']))
        fresh = prep.read(result['artifact'])
        self.assertNotEqual(fresh['identity']['base_sha'], parent['identity']['base_sha'])
        self.assertEqual(fresh['identity']['head_sha'], parent['identity']['head_sha'])
        self.assertEqual(fresh['inputs']['tree'], parent['inputs']['tree'])
        self.assertEqual(result['refresh']['changed_paths'], [])
        self.assertEqual(result['refresh']['changed_sources'], ['context'])
        self.assertEqual(result['refresh']['mode'], 'full')
        self.assertEqual(result['refresh']['model_call_count'], 1)
        self.assertEqual(set(result['refresh']['affected_conclusions']), set(parent['graph']))

    def test_contract_mode_and_policy_changes_require_full_dispositions(self):
        first = self.package.prepare()
        parent = prep.read(first['artifact'])
        contract = Path(prep.__file__).resolve().parents[1] / 'references/preparation-contract.md'
        file_hash = prep.file_hash
        with patch.object(prep, 'file_hash', side_effect=lambda path: '0' * 64 if Path(path) == contract else file_hash(path)):
            result = self.refresh(first, retain(parent))
            self.assertEqual(result['refresh']['mode'], 'full')
            self.assertEqual(result['refresh']['model_call_count'], 1)
            self.assertEqual(set(result['refresh']['affected_conclusions']), set(parent['graph']))
        self.package.manifest['mode'] = 'iteration'
        self.package.write('manifest.json', self.package.manifest)
        result = self.refresh(first, retain(parent, unmapped=['source:manifest']))
        self.assertEqual(result['refresh']['mode'], 'full')
        self.assertEqual(set(result['refresh']['affected_conclusions']), set(parent['graph']))
        with patch.object(prep, 'policy', return_value='new-policy'):
            result = self.refresh(first, retain(parent, unmapped=['source:manifest']))
        self.assertEqual(result['refresh']['mode'], 'full')
        # Explicit full reassessment cannot be skipped on otherwise identical inputs.
        self.package.manifest['mode'] = 'standalone'
        self.package.write('manifest.json', self.package.manifest)
        result = self.refresh(first, retain(parent), full_analysis=True)
        self.assertEqual(result['refresh']['mode'], 'full')

    def test_old_policy_parent_reaches_full_reassessment_without_current_normalization(self):
        semantic, identity = graph.semantic, graph.source_identity
        def old_semantic(key, content):
            value = semantic(key, content)
            return {'old_normalization': value} if key == 'user_instructions/0' else value
        def old_identity(key, source, checkout, content):
            return 'old-ci-identity' if key == 'ci/0' else identity(key, source, checkout, content)
        with patch.object(prep, 'policy', return_value='old-policy'), \
                patch.object(graph, 'semantic', side_effect=old_semantic), \
                patch.object(graph, 'source_identity', side_effect=old_identity):
            first = self.package.prepare()
        parent = prep.read(first['artifact'])
        with patch.object(graph, 'GOVERNING', graph.GOVERNING | {'tests'}), \
                patch.object(prep, 'policy', return_value='new-policy'):
            self.assertEqual(prep.historical(first['artifact']), parent)
            with self.assertRaises(ValueError):
                prep.load(first['artifact'], self.package.context, self.package.decisions)
            result = self.refresh(first, retain(parent, unmapped=['source:ci/0', 'source:ci/1']))
            self.assertEqual(result['refresh']['mode'], 'full')
            self.assertEqual(result['refresh']['model_call_count'], 1)
            self.assertEqual(set(result['refresh']['affected_conclusions']), set(parent['graph']))
            fresh = prep.read(result['artifact'])
            self.assertEqual(fresh['inputs']['policy'], prep.policy())
            self.assertEqual(fresh['analysis'], parent['analysis'])
            for node in fresh['graph'].values():
                if node['location'].split('/')[1] not in graph.GUIDANCE:
                    self.assertIn('tests/0', node['sources'])
            for defect in ('coverage', 'watch', 'dependency', 'snapshot'):
                invalid = copy.deepcopy(parent)
                node = next(iter(invalid['graph'].values()))
                if defect == 'coverage':
                    invalid['graph'].pop(next(iter(invalid['graph'])))
                elif defect == 'watch':
                    node['paths'] = [{'path': '../outside', 'kind': 'file'}]
                elif defect == 'dependency':
                    node['depends_on'].append('c-missing')
                else:
                    invalid['sources']['production/0']['sha256'] = '0' * 64
                invalid['preparation_id'] = prep.digest({k: v for k, v in invalid.items() if k != 'preparation_id'})
                path = self.package.write('invalid-old-policy.json', invalid)
                with self.subTest(defect=defect), self.assertRaisesRegex(ValueError, 'invalid selected parent'):
                    prep.historical(path)
            for defect in ('fingerprint', 'mandatory'):
                corrupt = copy.deepcopy(fresh)
                if defect == 'fingerprint':
                    corrupt['inputs']['sources']['production/0']['governing'] = '0' * 64
                    error = 'governing input fingerprint mismatch'
                else:
                    for node in corrupt['graph'].values():
                        if 'tests/0' in node['sources']:
                            node['sources'].remove('tests/0')
                    error = 'mandatory governing dependencies missing'
                corrupt['preparation_id'] = prep.digest({k: v for k, v in corrupt.items() if k != 'preparation_id'})
                path = self.package.write('corrupt-current-policy.json', corrupt)
                with self.subTest(defect=defect), self.assertRaisesRegex(ValueError, error):
                    prep.historical(path)

    def test_current_missing_or_incomplete_feedback_blocks(self):
        first = self.package.prepare()
        self.package.write('feedback.json', {'complete': False, 'items': []})
        self.package.manifest['evidence']['feedback'] = ['feedback.json']
        self.package.write('manifest.json', self.package.manifest)
        with patch.object(prep, 'analyze') as model, self.assertRaisesRegex(ValueError, 'complete current feedback'):
            self.refresh(first)
        model.assert_not_called()
        (self.package.root / 'feedback.json').unlink()
        with self.assertRaises(OSError):
            self.refresh(first)

    def inline_feedback(self, resolved=False, text='Keep supported inputs', author='human'):
        comment = {'id': 'comment-1', 'reference': 'discussion_r1', 'text': text,
                   'author': author, 'url': 'https://github.com/example/fixture/pull/1#discussion_r1'}
        thread = {'id': 'thread-1', 'isResolved': resolved, 'path': 'transform.py', 'line': 1,
                  'originalLine': 1, 'isOutdated': False, 'comments': [comment]}
        item = {'reference': thread['id'], 'commentReference': comment['reference'], 'url': comment['url'],
                'path': thread['path'], 'line': 1, 'originalLine': 1, 'isOutdated': False,
                'text': comment['text'], 'conversation': thread['comments']}
        items = [] if resolved else [item]
        return {'schemaVersion': 1, 'complete': True,
                'pullRequest': {'url': self.package.context['url'], 'headRefOid': self.package.head},
                'threads': [thread], 'threadCount': 1, 'threadsSha256': graph.digest([thread]),
                'items': items, 'itemCount': len(items), 'itemsSha256': graph.digest(items)}

    def conversation_feedback(self):
        reader = module('conversation_fixture', 'dk-pr-address-reviews/scripts/fetch_pr_conversation.py')
        value = module('conversation_shape', 'dk-pr-decisions/tests/test_preparation.py').PreparationTests.empty_feedback(self)[1]
        raw = {'id': 1, 'body': 'Review constraint', 'user': {'login': 'human'},
               'html_url': self.package.context['url'] + '#pullrequestreview-1'}
        value['reviews'] = reader.normalize([raw], review=True)
        value['comments'] = reader.normalize([{**raw, 'id': 2, 'body': '',
            'html_url': self.package.context['url'] + '#issuecomment-2'}], review=False)
        value.update(reviewCount=1, commentCount=1, itemCount=2)
        value['items'] = copy.deepcopy(value['reviews'] + value['comments'])
        value['sha256'] = graph.digest({k: v for k, v in value.items() if k != 'sha256'})
        return value

    def test_missing_original_feedback_content_blocks_before_model(self):
        malformed = []
        for resolved in (False, True):
            for content in (None, {}, {'reference': 'comment-1'}, {'reference': 'comment-1', 'text': None}):
                value = self.inline_feedback(resolved=resolved)
                value['threads'][0]['comments'] = [content]
                malformed.append(value)
        for field, content in (('text', None), ('conversation', [None]), ('conversation', []),
                               ('conversation', [{'reference': 'comment-1'}])):
            value = self.inline_feedback()
            value['items'][0][field] = content
            malformed.append(value)
        for collection in ('reviews', 'comments', 'items'):
            for field in ('text', 'reference'):
                value = self.conversation_feedback()
                value[collection][0].pop(field)
                malformed.append(value)
            value = self.conversation_feedback()
            value[collection][0]['text'] = 7
            malformed.append(value)
        package = self.package
        package.manifest['evidence']['feedback'] = ['inline.json', 'conversation.json']
        package.write('manifest.json', package.manifest)
        for number, value in enumerate(malformed):
            if 'schemaVersion' in value:
                value['threadsSha256'] = graph.digest(value['threads'])
                value['itemsSha256'] = graph.digest(value['items'])
                inline, conversation = value, self.conversation_feedback()
            else:
                value['sha256'] = graph.digest({k: v for k, v in value.items() if k != 'sha256'})
                inline, conversation = self.inline_feedback(), value
            package.write('inline.json', inline)
            package.write('conversation.json', conversation)
            with self.subTest(number=number), patch.object(prep, 'analyze') as model:
                with self.assertRaisesRegex(ValueError, 'feedback|comment'):
                    prep.prepare(package.manifest_path, package.root / 'versions')
                model.assert_not_called()
        package.write('inline.json', self.inline_feedback())
        package.write('conversation.json', self.conversation_feedback())
        self.assertEqual(package.prepare()['readiness'], 'ready')

    def test_resolution_is_operational_but_edited_bot_duplicate_and_self_feedback_are_governing(self):
        package = self.package
        package.write('feedback.json', self.inline_feedback())
        package.manifest['evidence']['feedback'] = ['feedback.json']
        package.write('manifest.json', package.manifest)
        value = analysis()
        value['questions'] = [module('feedback_questions', 'dk-pr-decisions/tests/test_preparation.py').PreparationTests.question(self)]
        first = package.prepare(value)
        parent = prep.read(first['artifact'])
        package.write('feedback.json', self.inline_feedback(resolved=True))
        resolved = self.refresh(first)
        self.assertEqual(resolved['refresh']['model_call_count'], 0)
        self.assertEqual(prep.read(resolved['artifact'])['analysis']['questions'], value['questions'])
        self.assertEqual(resolved['readiness'], 'needs_decision')
        for author in ('bot', 'self', 'human'):
            package.write('feedback.json', self.inline_feedback(resolved=True, text='Changed exact requirement', author=author))
            result = self.refresh(first, retain(parent, unmapped=['source:feedback/0']))
            self.assertEqual(result['refresh']['model_call_count'], 1)
        duplicate = self.inline_feedback()
        duplicate['threads'].append(copy.deepcopy(duplicate['threads'][0]))
        duplicate['threadCount'] = 2
        duplicate['threadsSha256'] = graph.digest(duplicate['threads'])
        package.write('feedback.json', duplicate)
        with self.assertRaisesRegex(ValueError, 'duplicate feedback identity'):
            self.refresh(first)

    def test_unknown_feedback_fields_and_deleted_records_never_disappear_in_normalization(self):
        value = self.inline_feedback()
        normal = graph.semantic('feedback/0', json.dumps(value).encode())
        changed = copy.deepcopy(value)
        changed['threads'][0]['unknown_governing_field'] = 'new constraint'
        changed['threadsSha256'] = graph.digest(changed['threads'])
        self.assertNotEqual(normal, graph.semantic('feedback/0', json.dumps(changed).encode()))
        changed = copy.deepcopy(value)
        changed.update(threads=[], threadCount=0, threadsSha256=graph.digest([]),
                       items=[], itemCount=0, itemsSha256=graph.digest([]))
        self.assertNotEqual(normal, graph.semantic('feedback/0', json.dumps(changed).encode()))
        # Harmless JSON formatting is normalized for the supported collector schema.
        self.assertEqual(normal, graph.semantic('feedback/0', json.dumps(value, indent=4).encode()))

    def test_is_resolved_on_non_thread_feedback_records_remains_governing(self):
        conversation = self.conversation_feedback()
        cases = [(conversation, name) for name in ('reviews', 'comments', 'items')]
        cases.append((self.inline_feedback(), 'items'))
        for snapshot, collection in cases:
            versions = []
            for flag in (None, 'New unknown constraint', 'Edited unknown constraint', None):
                value = copy.deepcopy(snapshot)
                if flag is not None:
                    value[collection][0]['isResolved'] = flag
                if 'schemaVersion' in value:
                    value['itemsSha256'] = graph.digest(value['items'])
                else:
                    value['sha256'] = graph.digest({k: v for k, v in value.items() if k != 'sha256'})
                versions.append(graph.semantic('feedback/0', json.dumps(value).encode()))
            with self.subTest(schema=snapshot.get('schemaVersion', 'conversation'), collection=collection):
                self.assertNotEqual(versions[0], versions[1])  # Addition.
                self.assertNotEqual(versions[1], versions[2])  # Edit.
                self.assertNotEqual(versions[2], versions[3])  # Removal.
                self.assertEqual(versions[0], versions[3])

    def test_feedback_unknown_fields_are_specific_to_the_selected_schema(self):
        records = [{'label': 'second'}, {'label': 'first'}]
        for snapshot in (self.inline_feedback(), self.conversation_feedback()):
            fields = ('sha256', 'reviews', 'comments') if 'schemaVersion' in snapshot else (
                'threadsSha256', 'itemsSha256', 'threads')
            fields += ('evidence_version', 'preparation_history_version')
            for field in fields:
                versions = []
                added = records if field in {'reviews', 'comments', 'threads'} else 'Unknown first value'
                edited = list(reversed(records)) if isinstance(added, list) else 'Unknown edited value'
                if field in {'evidence_version', 'preparation_history_version'}:
                    added, edited = 1, 2
                for payload in (None, added, edited, None):
                    value = copy.deepcopy(snapshot)
                    if payload is not None:
                        value[field] = payload
                    if 'schemaVersion' not in value:
                        value['sha256'] = graph.digest({k: v for k, v in value.items() if k != 'sha256'})
                    versions.append(graph.semantic('feedback/0', json.dumps(value).encode()))
                with self.subTest(schema=snapshot.get('schemaVersion', 'conversation'), field=field):
                    self.assertNotEqual(versions[0], versions[1])
                    self.assertNotEqual(versions[1], versions[2])
                    self.assertNotEqual(versions[2], versions[3])
                    self.assertEqual(versions[0], versions[3])
                    self.assertEqual(versions[1][field], added)
                    self.assertEqual(versions[2][field], edited)

    def test_unknown_feedback_hash_fields_require_impact_reassessment(self):
        package = self.package
        package.manifest['evidence']['feedback'] = ['feedback.json']
        package.write('manifest.json', package.manifest)
        for snapshot, field in ((self.inline_feedback(), 'sha256'), (self.conversation_feedback(), 'itemsSha256')):
            package.write('feedback.json', snapshot)
            previous = package.prepare()
            for payload in ('Unknown first value', 'Unknown edited value', None):
                value = copy.deepcopy(snapshot)
                if payload is not None:
                    value[field] = payload
                if 'schemaVersion' not in value:
                    value['sha256'] = graph.digest({k: v for k, v in value.items() if k != 'sha256'})
                package.write('feedback.json', value)
                parent = prep.read(previous['artifact'])
                result = self.refresh(previous, retain(parent, unmapped=['source:feedback/0']))
                self.assertEqual(result['refresh']['model_call_count'], 1)
                self.assertEqual(result['refresh']['changed_sources'], ['feedback/0'])
                self.assertEqual(result['refresh']['mode'], 'incremental')
                previous = result

    def test_feedback_schema_marker_requires_the_supported_integer_type(self):
        package = self.package
        package.manifest['evidence']['feedback'] = ['feedback.json']
        package.write('manifest.json', package.manifest)
        for marker in (True, 1.0, '1', None, 2):
            package.write('feedback.json', {**self.inline_feedback(), 'schemaVersion': marker})
            with self.subTest(marker=marker), patch.object(prep, 'analyze') as model:
                with self.assertRaisesRegex(ValueError, 'unsupported complete feedback schema'):
                    prep.prepare(package.manifest_path, package.root / 'versions')
                model.assert_not_called()

    def test_unknown_complete_claims_in_other_categories_keep_exact_bytes(self):
        for category in prep.EVIDENCE:
            if category in {'feedback', 'reviewers', 'prior_preparation'}:
                continue
            key = category + '/0'
            for flag in ('true', 'false'):
                with self.subTest(category=category, complete=flag):
                    raw = ('{"complete":' + flag + '}').encode()
                    original = graph.semantic(key, raw)
                    self.assertEqual(original, {'opaque_sha256': hashlib.sha256(raw).hexdigest()})
                    self.assertNotEqual(original, graph.semantic(key, ('{ "complete": ' + flag + ' }').encode()))

    def test_question_resolution_requires_answer_or_precedence_evidence(self):
        package = self.package
        value = analysis()
        value['questions'] = [module('incremental_questions', 'dk-pr-decisions/tests/test_preparation.py').PreparationTests.question(self)]
        first = package.prepare(value)
        parent = prep.read(first['artifact'])
        package.write('user.json', {'instruction': 'Human explicitly selected option one'})
        decision = retain(parent)
        for item in decision['dispositions']:
            if parent['graph'][item['id']]['location'] == '/questions/0':
                item.update(action='replace', value={**value['questions'][0], 'status': 'resolved',
                            'resolution': 'Selected option one; exact answer retained in user_instructions/0'})
                chosen = item
                chosen['value']['evidence'] = ['feedback/0']
        with self.assertRaisesRegex(ValueError, 'answer or precedence'):
            self.refresh(first, decision)
        chosen['value']['evidence'] = ['user_instructions/0']
        result = self.refresh(first, decision)
        self.assertEqual(result['readiness'], 'ready')

    def test_disappeared_old_source_uses_retained_snapshot(self):
        package = self.package
        package.write('user.json', {'evidence_version': 1, 'source_id': 'user', 'governing': {'text': 'Same choice'}})
        first = package.prepare()
        (package.root / 'user.json').rename(package.root / 'new-user.json')
        package.manifest['evidence']['user_instructions'] = ['new-user.json']
        package.write('manifest.json', package.manifest)
        result = self.refresh(first)
        self.assertEqual(result['refresh']['model_call_count'], 0)
        self.assertEqual(prep.historical(first['artifact'])['analysis'], analysis())
        snapshot = prep.read(first['artifact'])['sources']['user_instructions/0']['snapshot']
        Path(snapshot).chmod(0o600)
        Path(snapshot).write_text('corrupt retained source')
        with self.assertRaisesRegex(ValueError, 'parent evidence snapshot changed'):
            self.refresh(first)

    def test_selected_parent_snapshots_are_revalidated_after_analysis_and_copying(self):
        package = self.package
        first = package.prepare()
        initial = prep.read(first['artifact'])
        second = self.refresh(first, retain(initial), full_analysis=True)
        parent = prep.read(second['artifact'])
        self.assertEqual(package.manifest['evidence']['prior_preparation'], ['absence.json'])
        snapshots = {Path(parent['sources']['production/0']['snapshot']), Path(initial['sources']['production/0']['snapshot'])}
        write_bytes = Path.write_bytes
        for target, snapshot in (('selected', Path(parent['sources']['production/0']['snapshot'])),
                                 ('ancestor', Path(initial['sources']['production/0']['snapshot'])),
                                 ('ancestor_json', Path(first['artifact']))):
            original = snapshot.read_bytes()
            for phase, defect in itertools.product(('analyzer', 'copy'), ('delete', 'corrupt')):
                damaged = []
                def damage():
                    damaged.append(True)
                    if defect == 'delete':
                        snapshot.unlink()
                    else:
                        snapshot.chmod(0o600)
                        write_bytes(snapshot, b'corrupt selected parent evidence')
                def analyzer(*_):
                    if phase == 'analyzer':
                        damage()
                    return retain(parent)
                def copying(path, content):
                    result = write_bytes(path, content)
                    if phase == 'copy' and path.parts[-3:] == ('evidence', 'production', '0') and path not in snapshots:
                        damage()
                    return result
                try:
                    with self.subTest(target=target, phase=phase, defect=defect), patch.object(Path, 'write_bytes', copying), \
                            patch.object(prep, 'policy', return_value=parent['inputs']['policy'] if phase == 'analyzer' else 'new-policy'):
                        with self.assertRaisesRegex(ValueError, 'invalid selected parent'):
                            prep.prepare(package.manifest_path, package.root / 'versions',
                                         previous_preparation=second['artifact'], full_analysis=True, analyzer=analyzer)
                        self.assertEqual(len(damaged), 1)
                        self.assertEqual(set((package.root / 'versions').glob('*/preparation.json')),
                                         {Path(first['artifact']), Path(second['artifact'])} -
                                         ({snapshot} if defect == 'delete' else set()))
                finally:
                    if snapshot.exists():
                        snapshot.chmod(0o600)
                    write_bytes(snapshot, original)
                    snapshot.chmod(0o400)
        self.assertEqual(prep.historical(second['artifact']), parent)

    def test_missing_old_git_object_forces_full_and_parent_changes_reject_result(self):
        first = self.package.prepare()
        parent = prep.read(first['artifact'])
        original_git = prep.git
        def missing(checkout, *args):
            if args[0] == 'cat-file':
                raise ValueError('old object no longer available')
            return original_git(checkout, *args)
        with patch.object(prep, 'git', side_effect=missing):
            result = self.refresh(first, retain(parent))
        self.assertEqual(result['refresh']['mode'], 'full')
        self.package.write('user.json', {'instruction': 'Changed governing instruction'})
        def changed_parent(*_):
            Path(first['artifact']).chmod(0o600)
            Path(first['artifact']).write_text('changed selected parent')
            return retain(parent)
        with self.assertRaisesRegex(ValueError, 'selected parent changed'):
            prep.prepare(self.package.manifest_path, self.package.root / 'versions',
                         previous_preparation=first['artifact'], analyzer=changed_parent)

    def test_review_session_resume_still_requires_exact_identity(self):
        from shared.review_sessions import Session
        first = self.package.prepare()
        parent = prep.read(first['artifact'])
        path = self.package.root / 'review-session.json'
        Session(self.package.context, parent, ['example'], path=path)
        self.commit({}, amend=True)
        fresh = self.refresh(first)
        self.assertEqual(fresh['refresh']['model_call_count'], 0)
        with self.assertRaisesRegex(ValueError, 'changed evidence'):
            Session(self.package.context, prep.read(fresh['artifact']), ['example'], path=path, resume=True)

    def test_growing_lineage_does_not_invalidate_itself_or_change_analysis(self):
        package = self.package
        first = package.prepare()
        package.manifest['evidence']['prior_preparation'] = [first['artifact']]
        package.write('manifest.json', package.manifest)
        second = self.refresh(first)
        self.assertEqual(second['refresh']['model_call_count'], 0)
        self.assertEqual(prep.read(second['artifact'])['analysis'], analysis())
        package.manifest['evidence']['prior_preparation'] = [second['artifact']]
        package.write('manifest.json', package.manifest)
        third = self.refresh(second)
        self.assertEqual(third['refresh']['model_call_count'], 0)
        self.assertEqual(prep.read(third['artifact'])['analysis'], analysis())
        self.assertEqual(prep.historical(third['artifact'])['preparation_id'], third['preparation_id'])
        package.manifest['evidence']['prior_preparation'] = ['absence.json', first['artifact'], second['artifact'], third['artifact']]
        package.write('manifest.json', package.manifest)
        grown = self.refresh(third)
        self.assertEqual(grown['refresh']['model_call_count'], 0)
        package.manifest['evidence']['prior_preparation'] = ['absence.json']
        package.write('manifest.json', package.manifest)
        self.assertEqual(self.refresh(grown)['refresh']['model_call_count'], 0)

    def test_prior_absence_edits_additions_removals_and_replacements_are_assessed(self):
        package = self.package
        absence = {'verified_absence': True, 'reason': 'Verified no previous source'}
        original = package.write('prior-original.json', absence)
        package.manifest['evidence']['prior_preparation'] = [str(original)]
        package.write('manifest.json', package.manifest)
        first = package.prepare()
        current = package.write('prior-current.json', absence)
        original.unlink()
        package.manifest['evidence']['prior_preparation'] = [str(current)]
        package.write('manifest.json', package.manifest)
        previous = self.refresh(first)
        self.assertEqual(previous['refresh']['model_call_count'], 0)
        for value in ({**absence, 'reason': 'Changed absence assumption'},
                      {**absence, 'reason': 'Changed absence assumption', 'constraint': 'Inspect this'},
                      {**absence, 'reason': 'Changed absence assumption', 'constraint': 'Edited constraint'},
                      {**absence, 'reason': 'Changed absence assumption'}, absence):
            package.write('prior-current.json', value)
            parent = prep.read(previous['artifact'])
            previous = self.refresh(previous, retain(parent, unmapped=['source:prior_preparation/0']))
            self.assertEqual(previous['refresh']['model_call_count'], 1)
            self.assertEqual(previous['refresh']['changed_sources'], ['prior_preparation/0'])
        added = package.write('prior-new.json', {**absence, 'reason': 'A newly verified absence'})
        for paths in ([str(current), str(added)], [str(current)], [str(current), str(current)], [str(current)]):
            package.manifest['evidence']['prior_preparation'] = paths
            package.write('manifest.json', package.manifest)
            parent = prep.read(previous['artifact'])
            previous = self.refresh(previous, retain(parent, unmapped=['source:prior_preparation/1']))
            self.assertEqual(previous['refresh']['model_call_count'], 1)
            self.assertEqual(previous['refresh']['changed_sources'], ['prior_preparation/1'])

    def test_duplicate_absence_changes_remain_visible_alongside_lineage(self):
        package = self.package
        first = package.prepare()
        package.manifest['evidence']['prior_preparation'] = ['absence.json', first['artifact']]
        package.write('manifest.json', package.manifest)
        previous = self.refresh(first)
        self.assertEqual(previous['refresh']['model_call_count'], 0)
        for paths, changed in ((['absence.json', first['artifact'], 'absence.json'], ['prior_preparation/2']),
                               (['absence.json', first['artifact']], ['prior_preparation/2']),
                               (['absence.json', 'absence.json'], ['prior_preparation/1'])):
            package.manifest['evidence']['prior_preparation'] = paths
            package.write('manifest.json', package.manifest)
            parent = prep.read(previous['artifact'])
            previous = self.refresh(previous, retain(parent, unmapped=['source:' + key for key in changed]))
            self.assertEqual(previous['refresh']['model_call_count'], 1)
            self.assertEqual(previous['refresh']['changed_sources'], changed)
        package.manifest['evidence']['prior_preparation'] = [previous['artifact']]
        package.write('manifest.json', package.manifest)
        parent = prep.read(previous['artifact'])
        result = self.refresh(previous, retain(parent, unmapped=['source:prior_preparation/0', 'source:prior_preparation/1']))
        self.assertEqual(result['refresh']['model_call_count'], 1)
        self.assertEqual(result['refresh']['changed_sources'], ['prior_preparation/0', 'prior_preparation/1'])

    def test_new_absence_cannot_hide_when_replacing_a_lineage_slot(self):
        package = self.package
        first = package.prepare()
        package.manifest['evidence']['prior_preparation'] = [first['artifact']]
        package.write('manifest.json', package.manifest)
        second = self.refresh(first)
        self.assertEqual(second['refresh']['model_call_count'], 0)
        path = package.write('new-absence.json', {'verified_absence': True, 'reason': 'New absence',
                                                'unknown_constraint': 'Must be inspected'})
        package.manifest['evidence']['prior_preparation'] = [str(path), second['artifact']]
        package.write('manifest.json', package.manifest)
        parent = prep.read(second['artifact'])
        result = self.refresh(second, retain(parent, unmapped=['source:prior_preparation/0']))
        self.assertEqual(result['refresh']['model_call_count'], 1)
        self.assertEqual(result['refresh']['changed_sources'], ['prior_preparation/0'])

    def test_non_lineage_prior_removals_and_replacements_require_assessment(self):
        package = self.package
        unrelated = package.prepare()
        replacement = package.prepare()
        package.manifest['evidence']['prior_preparation'] = ['absence.json', unrelated['artifact']]
        package.write('manifest.json', package.manifest)
        first = package.prepare()
        parent = prep.read(first['artifact'])
        Path(unrelated['artifact']).unlink()
        for prior in (['absence.json'], ['absence.json', 'absence.json'], ['absence.json', replacement['artifact']]):
            package.manifest['evidence']['prior_preparation'] = prior
            package.write('manifest.json', package.manifest)
            with self.subTest(prior=prior):
                result = self.refresh(first, retain(parent, unmapped=['source:prior_preparation/1']))
                self.assertEqual(result['refresh']['model_call_count'], 1)
                self.assertEqual(result['refresh']['mode'], 'incremental')
                self.assertEqual(result['refresh']['changed_sources'], ['prior_preparation/1'])

    def test_real_context_transport_head_changes_still_use_zero_calls(self):
        package = self.package
        package.context.update(local_head=package.head, local_matches_pr=True, worktree_clean=True)
        package.write('context.json', package.context)
        package.write('metadata.json', {'body': '', 'title': 'Transform fix', 'headRefOid': package.head})
        first = package.prepare()
        self.commit({}, amend=True)
        package.context['local_head'] = package.head
        package.write('context.json', package.context)
        package.write('metadata.json', {'body': '', 'title': 'Transform fix', 'headRefOid': package.head})
        refreshed = self.refresh(first)
        self.assertEqual(refreshed['refresh']['model_call_count'], 0)
        self.assertIn('metadata', refreshed['refresh']['changed_bindings'])

    def test_second_invalid_reassessment_blocks_without_publishing(self):
        first = self.package.prepare()
        self.package.write('user.json', {'instruction': 'A new human constraint'})
        for invalid in ({'dispositions': []}, {'dispositions': [{'id': []}], 'additions': [], 'impacts': []}):
            with patch('shared.executors.execute', return_value=json.dumps(invalid)) as model:
                with self.assertRaisesRegex(ValueError, 'invalid after one correction'):
                    prep.prepare(self.package.manifest_path, self.package.root / 'versions', previous_preparation=first['artifact'])
            self.assertEqual(model.call_count, 2)
        self.assertEqual(list((self.package.root / 'versions').glob('*/preparation.json')), [Path(first['artifact'])])

    def test_reviewer_instruction_changes_and_new_rules_invalidate_governing_conclusions(self):
        package = self.package
        first = package.prepare()
        parent = prep.read(first['artifact'])
        descriptor = prep.read(package.root / 'reviewer.json')
        descriptor['effective_prompt'] += ' Check an additional explicit obligation.'
        package.write('reviewer.json', descriptor)
        changed = self.refresh(first, retain(parent))
        self.assertEqual(set(changed['refresh']['affected_conclusions']), set(parent['graph']))
        self.assertEqual(changed['refresh']['model_call_count'], 1)
        package.write('new-rules.json', {'rule': 'A new applicable engineering constraint'})
        package.manifest['evidence']['rules'].append('new-rules.json')
        package.write('manifest.json', package.manifest)
        changed = self.refresh(first, retain(parent, unmapped=['source:rules/1']))
        self.assertEqual(set(changed['refresh']['affected_conclusions']), set(parent['graph']))
        self.assertIn('source:rules/1', changed['refresh']['unmapped_changes'])


class GitEntriesTests(unittest.TestCase):
    def test_canonical_digest_preserves_unicode_and_lossless_git_paths(self):
        self.assertEqual(graph.digest({'路径': 'café/😀.py'}),
                         '897ac0633727f2f346163ea0119ab799a49cfbbaa43feb3a0737031f1ae53b78')
        path = b'odd-\xff.py'.decode('utf-8', 'surrogateescape')
        self.assertNotEqual(graph.digest({'path': path}), graph.digest({'path': r'odd-\udcff.py'}))
        for path in ('café/😀.py', b'odd-\xff/file-\xfe.py'.decode('utf-8', 'surrogateescape')):
            for kind in ('file', 'subtree'):
                graph.watch_path({'path': path, 'kind': kind})

    def test_modes_binary_symlink_submodule_rename_and_delete(self):
        with tempfile.TemporaryDirectory() as root:
            package = Package(root)
            (package.repo / 'binary').write_bytes(b'\x00\xff')
            (package.repo / 'link').symlink_to('transform.py')
            prep.git(package.repo, 'add', '.')
            prep.git(package.repo, 'update-index', '--add', '--cacheinfo', '160000,' + package.head + ',submodule')
            prep.git(package.repo, 'commit', '-qm', 'Extra entry types')
            old = graph.entries(package.repo, 'HEAD')
            (package.repo / 'transform.py').chmod(0o755)
            (package.repo / 'binary').rename(package.repo / 'renamed')
            (package.repo / 'link').unlink()
            prep.git(package.repo, 'add', '-A')
            prep.git(package.repo, 'commit', '-qm', 'Entry updates')
            new = graph.entries(package.repo, 'HEAD')
            paths, _ = graph.changes({'inputs': {'tree': old, 'sources': {}}}, {'tree': new, 'sources': {}})
            self.assertEqual(paths, ['binary', 'link', 'renamed', 'submodule', 'transform.py'])
            self.assertEqual(old['submodule'][1], 'commit')
            self.assertNotEqual(old['transform.py'][0], new['transform.py'][0])
