"""Bind exported Decisions to recorded agreements; freshness checks are read-only."""
import hashlib
import importlib.util
import json
from pathlib import Path


def binding(records, store_root):
    # Publication bookkeeping never changes the agreement or its exact evidence.
    records = sorted(({k: v for k, v in record.items() if k != 'publication_status'}
                      for record in records), key=lambda record: record['id'])
    return {'version': 1, 'store_root': str(Path(store_root).resolve()),
            'decision_ids': [record['id'] for record in records],
            'fingerprint': hashlib.sha256(json.dumps(records, sort_keys=True,
                ensure_ascii=True, separators=(',', ':')).encode()).hexdigest()}


def check(context):
    """No scan, migration, locking, model invocation or store mutation is allowed here."""
    try:
        exported = context.get('agreement_binding')
        if (not isinstance(exported, dict) or type(exported.get('version')) is not int
                or exported['version'] != 1):
            raise ValueError('bound Decisions context required')
        path = Path(__file__).resolve().parents[1] / 'dk-pr-decisions/scripts/decisions.py'
        spec = importlib.util.spec_from_file_location('freshness_decisions', path)
        api = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(api)
        root = api.storage.root()
        if exported.get('store_root') != str(root):
            raise ValueError('selected Decisions store changed')
        state = api.storage.read(api.storage.cache_path(context['pr_url']), context['pr_url'])
        if (state.get('publication_intent') or {}).get('status') == 'pending':
            raise ValueError('Decisions publication is pending; use publish --reconcile')
        if exported != binding(api.decision_records(state), root):
            raise ValueError('recorded agreements changed')
    except (OSError, ValueError, RuntimeError, KeyError, TypeError) as exc:
        raise ValueError(f'Decisions freshness check failed: {exc}; reconcile the Decisions store '
                         'as needed, then refresh context and preparation before dependent work') from exc
