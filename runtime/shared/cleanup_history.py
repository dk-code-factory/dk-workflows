"""Strict machine cleanup bookkeeping. Classification never supplies a gate."""
import hashlib
import json
from pathlib import Path
import re


def shape(value):
    fields = {'cleanup_version', 'kind', 'run_id', 'head_sha', 'worker',
              'preparation_id', 'engineering_id', 'artifacts'}
    retained = {'artifact', 'sha256'}
    if (not isinstance(value, dict) or not fields <= value.keys()
            or value.keys() - fields - retained or value.get('cleanup_version') != 1
            or type(value.get('cleanup_version')) is not int
            or value.get('kind') not in {'handoff', 'prepared', 'assessment'}
            or any(not isinstance(value[k], str) or not value[k].strip()
                   for k in fields - {'cleanup_version', 'artifacts'})
            or not re.fullmatch('[0-9a-f]{40}', value['head_sha'])
            or any(not re.fullmatch('[0-9a-f]{64}', value[k])
                   for k in ('preparation_id', 'engineering_id'))):
        return False
    refs = value['artifacts']
    required = {'preparation'} | ({'guard'} if value['kind'] != 'handoff' else set())
    if value['kind'] == 'assessment':
        required.add('assessment')
    return (isinstance(refs, dict) and required <= refs.keys()
            and refs.keys() <= {'preparation', 'guard', 'assessment', 'recovery'}
            and all(isinstance(ref, dict) and set(ref) == {'path', 'sha256'}
                    and isinstance(ref['path'], str) and Path(ref['path']).is_absolute()
                    and isinstance(ref['sha256'], str) and re.fullmatch('[0-9a-f]{64}', ref['sha256'])
                    for ref in refs.values()))


def verify(value, *, retained=True):
    if not shape(value):
        raise ValueError('invalid typed cleanup record')
    for ref in value['artifacts'].values():
        if hashlib.sha256(Path(ref['path']).read_bytes()).hexdigest() != ref['sha256']:
            raise ValueError('cleanup artifact fingerprint changed')
    if retained:
        raw = Path(value['artifact']).read_bytes()
        if (hashlib.sha256(raw).hexdigest() != value['sha256']
                or json.loads(raw) != {k: v for k, v in value.items() if k not in {'artifact', 'sha256'}}):
            raise ValueError('cleanup machine evidence changed')
    return value
