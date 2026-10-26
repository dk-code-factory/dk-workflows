"""Prepublication receipts for iteration cleanup; never completion coverage."""
from pathlib import Path

from shared import review_coverage as coverage, series_assessment as series
from shared.private_state import read


def _object(value, label):
    if not isinstance(value, dict):
        raise ValueError('cleanup precheck ' + label + ' must be an object')
    return value


def _receipt(path):
    saved = _object(read(Path(path), {}), 'receipt')
    binding = _object(saved.get('binding', {}), 'binding')
    return binding, _object(binding.get('assessment', {}), 'assessment')


def stable_guard(data):
    # Publication bookkeeping changes after the check. All ownership, lease,
    # target and recovery fields remain part of the stable guard identity.
    return {key: value for key, value in _object(data, 'guard').items()
            if key not in {'publication', 'final_head', 'assessment', 'cleanup_precheck'}}


def identity(request, context, result, prepared, *, live=False):
    path = Path(request['split_state']).resolve()
    repo = context['repository']
    expected = Path(coverage.git(repo, 'rev-parse', '--path-format=absolute',
                                 '--git-path', 'pr-split-state.json')).resolve()
    if path != expected:
        raise ValueError('cleanup precheck must use the repository split guard state')
    data = _object(read(path, {}), 'guard')
    for field in ('remote', 'remote_branch', 'recovery_ref'):
        if not isinstance(data.get(field), str) or not data[field].strip():
            raise ValueError('cleanup precheck guard requires nonempty ' + field)
    candidate = request['candidate_head']
    head = prepared['identity']['head_sha']
    tree = coverage.git(repo, 'rev-parse', f'{candidate}^{{tree}}')
    if (data.get('mode') != 'regroup' or data.get('original_head') != head
            or result['head'] != head or data.get('remote_sha') != head
            or data.get('pr_url') != result['pr_url'] or data.get('original_tree') != tree
            or tree != result['tree'] or data.get('base') != prepared['identity']['base_sha']
            or data.get('target_tip') != data['base'] or data.get('publication')
            or coverage.git(repo, 'rev-parse', 'HEAD') != candidate
            or coverage.git(repo, 'status', '--porcelain=v1')
            or coverage.git(repo, 'symbolic-ref', '--short', 'HEAD') != data.get('branch')
            or coverage.git(repo, 'merge-base', candidate, data['base']) != data['base']
            or coverage.git(repo, 'rev-parse', data.get('recovery_ref', '')) != head):
        raise ValueError('cleanup precheck guard, candidate, reviewed head/tree or base changed')
    if Path(context['checkout']).resolve() == Path(repo).resolve():
        raise ValueError('cleanup precheck requires an isolated reviewed-head checkout')
    if live:
        current = series.live_snapshot(data['pr_url'], context['checkout'])
        if (current['final_head'] != head or current['final_tree'] != tree
                or current['target_tip'] != data['target_tip']
                or current['target_branch'] != data.get('target_branch')
                or current['head_repo'] != data.get('head_repo')):
            raise ValueError('cleanup precheck published head, tree, repository or target changed')
        push_url = coverage.git(repo, 'remote', 'get-url', '--push', data['remote'])
        lease = coverage.git(repo, 'ls-remote', '--heads', push_url, data['remote_branch']).split()
        if not lease or lease[0] != head:
            raise ValueError('cleanup precheck remote lease changed')
    return {'split_state': str(path), 'guard': stable_guard(data),
            'guard_hash': coverage.digest(stable_guard(data)), 'candidate_head': candidate,
            'candidate_tree': tree, 'record_path': str(Path(request['record_path']).resolve())}


def precheck(record_path, head, candidate_head, split_state, api):
    from shared import review_reconciliation as reconciliation
    request = {'split_state': str(Path(split_state).resolve()), 'candidate_head': candidate_head,
               'record_path': str(Path(record_path).resolve())}
    result = reconciliation.reconcile(record_path, head, None, api, precheck=request)
    if result['status'] == 'reconciled':
        result = {**result, 'status': 'prechecked', 'precheck': result['reconciliation'],
                  'review_ready': True, 'cleanup_ready': True, 'completion_ready': False}
        result.pop('reconciliation')
        result['reused_precheck'] = result.pop('reused_reconciliation')
    else:
        result.update(review_ready=False, cleanup_ready=False, completion_ready=False)
    return result


def validate(path, split_state, candidate_head, api):
    from shared import review_reconciliation as reconciliation
    path = Path(path).resolve()
    binding, request = _receipt(path)
    if binding.get('purpose') != 'cleanup_precheck':
        raise ValueError('cleanup requires a prepublication precheck receipt')
    if (request['split_state'] != str(Path(split_state).resolve())
            or request['candidate_head'] != candidate_head):
        raise ValueError('cleanup precheck does not cover this guard and candidate')
    record = _object(read(Path(request['record_path']), {}), 'run record')
    item = next((item for item in record.get('cleanup_prechecks', [])
                 if item['path'] == str(path)), None)
    if item is None:
        raise ValueError('cleanup precheck is unregistered')
    fresh, _, _, _ = reconciliation.snapshot(record, binding['final_head'], None, api,
                                             live=True, precheck=request)
    reconciliation.load(item, fresh)
    selected = reconciliation.retained_comparison(record, fresh, collection='cleanup_prechecks')
    if not selected or not selected[1]['equivalent']:
        raise ValueError('cleanup precheck is blocked')
    return item


def published_receipt(data, split_state):
    """Recover an attempted push without replaying its prepublication checks."""
    from shared import review_reconciliation as reconciliation
    item = _object(data, 'guard').get('cleanup_precheck')
    if not isinstance(item, dict):
        raise ValueError('cleanup publication lacks its prepublication receipt; retain recovery state')
    binding, request = _receipt(item['path'])
    reconciliation.load(item, binding)
    record = _object(read(Path(request['record_path']), {}), 'run record')
    if (binding.get('purpose') != 'cleanup_precheck'
            or item not in record.get('cleanup_prechecks', [])
            or binding['iteration_run_id'] != _object(record.get('iteration_run', {}), 'iteration run').get('id')
            or request.get('split_state') != str(Path(split_state).resolve())
            or request.get('guard') != stable_guard(data)
            or request.get('candidate_head') != data.get('final_head')
            or request.get('candidate_tree') != data.get('original_tree')):
        raise ValueError('cleanup publication precheck provenance changed; retain recovery state')
    return item


def review_for(path, record_path, prepared, api):
    """Readiness before publication only. Final coverage uses reconciliation."""
    binding, request = _receipt(path)
    if (request.get('record_path') != str(Path(record_path).resolve())
            or binding.get('current_preparation_id') != prepared['preparation_id']):
        raise ValueError('cleanup precheck does not cover this run and preparation')
    item = validate(path, request['split_state'], request['candidate_head'], api)
    record = _object(read(Path(record_path), {}), 'run record')
    reference, result = api.current_review(record, prepared)
    acceptances, _ = api.review_acceptances(record, reference, result)
    accepted = sorted({name for entry in acceptances for name in entry['reviewers']})
    return {**result, 'retained_coverage': {'precheck': item, 'executed_reviewers': [],
            'completion_ready': False}, **({'accepted_failures': accepted,
            'failure_acceptances': acceptances} if accepted else {})}
