"""Required review provenance, independent of best-effort reviewer analytics."""
import hashlib
import json
import subprocess
from pathlib import Path

from shared.private_state import read, mutate


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def git(cwd, *args):
    return subprocess.check_output(['git', *args], cwd=cwd, text=True).strip()


def active_run(record):
    run = record.get('iteration_run', {})
    if not run.get('id') or run.get('status') != 'active':
        raise ValueError('an active iteration run is required')
    return run['id']


def scope(prepared):
    raw = read(Path(prepared['sources']['decisions']['path']), {})
    decisions = raw.get('data', raw)
    # Head changes alone are allowed for proven tree-equivalent regrouping.
    return digest({'decisions': {k: v for k, v in decisions.items() if k != 'head_sha'},
                   'analysis': prepared['analysis'], 'reviewers': prepared['reviewers'],
                   'base_sha': prepared['identity']['base_sha'],
                   'scope_sources': {key: value['sha256'] for key, value in prepared['sources'].items()
                                     if key.split('/')[0] in {'issues', 'choices', 'user_instructions', 'rules'}}})


def cycle(record, head):
    active_run(record)
    cycles = record.get('cycles', [])
    if not cycles:
        raise ValueError('record a review cycle before launching reviewers')
    last = cycles[-1]
    retries = [r for r in record.get('cycle_retries', []) if r['number'] == last['number']]
    expected = retries[-1]['head'] if retries else last['head']
    if expected != head:
        raise ValueError('review head differs from recorded cycle')
    return last['number']


def retain(record_path, context, prepared, result, run_id, number):
    """Retain the runner's actual result and register its immutable fingerprint."""
    for key in ('repository', 'checkout'):
        if Path(record_path).resolve().is_relative_to(Path(context[key]).resolve()):
            raise ValueError('iteration record must stay outside the repository and checkout')
    artifact = {'version': 1, 'iteration_run_id': run_id, 'cycle': number,
                'pr_url': context['url'], 'head': context['head_sha'],
                'tree': git(context['checkout'], 'rev-parse', f"{context['head_sha']}^{{tree}}"),
                'scope': scope(prepared), 'preparation_id': prepared['preparation_id'],
                'result': result}
    fingerprint = digest(artifact)
    path = Path(record_path).resolve().parent / 'review-results' / (fingerprint + '.json')
    def immutable(old):
        if old != artifact:
            raise ValueError('review result artifact changed')
    mutate(path, artifact, immutable)
    item = {'path': str(path), 'sha256': fingerprint}
    def register(record):
        if active_run(record) != run_id or cycle(record, context['head_sha']) != number:
            raise ValueError('iteration run changed during review')
        results = record.setdefault('review_results', [])
        if item not in results:
            results.append(item)
    mutate(Path(record_path), {}, register)
    return item


def verify(record_path, prepared):
    """Require current-tree coverage and assessed findings from this iteration run."""
    record = read(Path(record_path), {})
    run_id = active_run(record)
    if not record.get('review_results'):
        raise ValueError('no configured-reviewer results in this iteration run')
    item = record['review_results'][-1]
    artifact = read(Path(item['path']), {})
    if digest(artifact) != item['sha256'] or artifact.get('version') != 1:
        raise ValueError('review result artifact changed')
    context = read(Path(prepared['sources']['context']['path']), {})
    tree = git(context['checkout'], 'rev-parse', f"{prepared['identity']['head_sha']}^{{tree}}")
    if (artifact['iteration_run_id'] != run_id
            or artifact['pr_url'] != prepared['identity']['pr_url']
            or artifact['tree'] != tree or artifact['scope'] != scope(prepared)
            or not record.get('cycles') or record['cycles'][-1]['number'] != artifact['cycle']):
        raise ValueError('final tree or scope lacks review coverage in this iteration run')
    result = artifact['result']
    completed = result['executed_reviewers'] + result['reused_reviewers']
    skipped = result['skipped_reviewers']
    if (result.get('complete') is not True or not completed
            or len(set(completed)) != len(completed)
            or any(not s.get('reason') for s in skipped)
            or set(completed) & {s['reviewer'] for s in skipped}
            or set(completed) | {s['reviewer'] for s in skipped} != set(prepared['reviewers'])):
        raise ValueError('configured-reviewer execution is incomplete')
    for finding in result['findings']:
        reference = 'review-finding:' + digest(finding)
        classification = record.get('threads', {}).get(reference, {}).get(artifact['head'])
        assessment = next((a for a in record.get('assessments', [])
                           if a['reference'] == reference and a['head'] == artifact['head']
                           and a['preparation_id'] == artifact['preparation_id']), None)
        if not classification or not assessment:
            raise ValueError('review finding lacks validity and scope assessment: ' + reference)
        if assessment['disposition'] == 'decision_blocked' or (
                assessment['disposition'] == 'within_scope'
                and classification['disposition'] not in {'no_change', 'deferred'}):
            raise ValueError('review finding remains actionable: ' + reference)
    return artifact
