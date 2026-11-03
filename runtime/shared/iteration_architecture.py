"""Immutable evidence for iteration's single architecture review before cycles."""
from pathlib import Path

from shared import architecture, review_coverage as coverage
from shared.private_state import mutate, read


def retain(directory, slot, prepared, review):
    value = {'version': 1, 'iteration_run_id': slot['iteration_run_id'],
             'pr_url': prepared['identity']['pr_url'],
             'preparation': {'path': slot['preparation'], 'id': prepared['preparation_id']},
             'review': review}
    fingerprint = coverage.digest(value)
    context = coverage.preparation_api().context_for(prepared)
    path = coverage.storage_path(Path(directory) / 'architecture-stage' / (fingerprint + '.json'), context)
    def immutable(old):
        if old != value:
            raise ValueError('iteration architecture stage changed')
    mutate(path, value, immutable)
    return {'path': str(path), 'sha256': fingerprint}


def verify(reference, prepared, *, run_id=None):
    if not isinstance(reference, dict) or set(reference) != {'path', 'sha256'}:
        raise ValueError('iteration architecture stage reference required')
    value = read(Path(reference['path']), {})
    if (coverage.digest(value) != reference['sha256'] or value.get('version') != 1
            or not value.get('iteration_run_id')
            or run_id is not None and value['iteration_run_id'] != run_id
            or prepared.get('mode') != 'iteration'
            or value.get('pr_url') != prepared['identity']['pr_url']):
        raise ValueError('iteration architecture stage identity changed')
    api = coverage.preparation_api()
    baseline = api.historical(value['preparation']['path'])
    if (baseline['preparation_id'] != value['preparation']['id']
            or baseline['identity']['pr_url'] != value['pr_url'] or baseline['mode'] != 'iteration'):
        raise ValueError('iteration architecture preparation changed')
    # Validate the original immutable inputs and code citations. Later cycles
    # review the current tree; this result describes only the pre-loop head.
    context = api.context_for(baseline)
    context['checkout'] = api.context_for(prepared)['checkout']
    raw = api.read(api.retained_source(Path(value['preparation']['path']), baseline, 'decisions'))
    reviewed = architecture.verify_review(value['review'], baseline['architecture'], context,
        raw.get('data', raw), live=False, allow_violations=True)
    return {**value, 'head': baseline['identity']['head_sha'],
            'findings': [item for item in reviewed['result']['coverage'] if item['status'] == 'violation']}
