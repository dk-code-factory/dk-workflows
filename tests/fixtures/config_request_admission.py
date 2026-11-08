"""Explicit offline admission fixture for tests of request-bound legacy commands."""
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[2]/'runtime'))
from config_requests import Requests


def admitted(operation, *, checkout=None, repository=None, upstream=None, pr=None):
    context = {'checkout': str(checkout or Path.cwd())}
    context.update({k: v for k, v in [('repository', repository), ('upstream', upstream), ('pr', pr)] if v is not None})
    store = Requests()
    request_id = store.start('Offline fixture: apply the explicitly supplied configuration operation.', context)['id']
    operations = [operation]
    preview = store.prepare(request_id, {'operations': operations})
    relations = []
    # Test fixtures deliberately replace same-scope overlapping settings. This is
    # synthetic user authorization, not native execution evidence.
    for old in preview['candidates']:
        new = next((d for d in preview['decisions'] if d['path'] == old['path']), None)
        if new:
            scope = operation.get('scope', 'catalog' if operation['domain'] == 'preferences' else 'repo')
            relations.append({'request': old['request'], 'decision': old['id'], 'new_decision': new['id'],
                              'choice': 'replace' if scope == old['scope'] else 'exception',
                              'evidence': 'Offline fixture explicitly replaces this setting.'})
    store.prepare(request_id, {'operations': operations, 'assessment': {
        'fingerprint': preview['fingerprint'], 'summary': 'Explicit offline fixture admission.',
        'reviewed': [d['request'] + '/' + d['id'] for d in preview['candidates']],
        'relations': relations, 'unresolved': []}})
    return request_id
