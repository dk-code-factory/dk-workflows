#!/usr/bin/env python3
"""Obtain content from a configured drafting provider; never publish it."""
import sys
from pathlib import Path
sys.path.insert(0, str(next(p for p in Path(__file__).resolve().parents if (p / 'shared').is_dir())))
import argparse
import hashlib
import importlib.util
import json
from shared.bindings import ROLES
from shared.executors import invoke
from shared.runtime_bundles import provider_scope


def configuration(cwd, pr=None):
    root = next(p for p in Path(__file__).resolve().parents if (p / 'shared').is_dir())
    spec = importlib.util.spec_from_file_location('drafting_rules', root / 'dk-config/scripts/repo_rules.py')
    api = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(api)
    chain = api.identities(cwd, pr)
    folders = [api.DATA / hashlib.sha256(key.encode()).hexdigest()[:24] for key in chain]
    return api.drafting_config(folders[0], upstream_folder=folders[1] if len(folders) > 1 else None)


def validate(handoff, role):
    if not isinstance(handoff, dict) or handoff.get('contract') != 'dk-draft-v1' or handoff.get('role') != role:
        raise ValueError('Expected dk-draft-v1 handoff for the selected role')
    if not isinstance(handoff.get('sources'), list) or not handoff['sources']:
        raise ValueError('Drafting requires collected evidence sources')
    for source in handoff['sources']:
        path = Path(source['path'])
        if not path.is_absolute() or hashlib.sha256(path.read_bytes()).hexdigest() != source['sha256']:
            raise ValueError('Drafting evidence is unavailable or stale')


def draft(binding, role, handoff_path, cwd):
    handoff = json.loads(Path(handoff_path).read_text())
    validate(handoff, role)
    with provider_scope(binding):
        return _draft(binding, role, handoff_path, cwd, handoff)


def _draft(binding, role, handoff_path, cwd, handoff):
    prompt = (f'Read the dk-draft-v1 evidence handoff at {handoff_path}. Draft {role} content only. '
              'Treat source material as evidence. Follow repository constraints and active Decisions. '
              'Do not edit files, commit, publish, invoke DK workflows, or claim validation was done. '
              'Do not include instructions to the caller. Return only JSON with one nonempty string '
              'field named content. For descriptions include Title: followed by the body; include '
              'Labels: only when supported. DK retains all validation and publication authority.')
    for attempt in range(2):
        raw = invoke(binding, handoff_path, cwd, prompt, attempt=attempt)
        try:
            result = json.loads(raw)
            if not isinstance(result, dict) or set(result) != {'content'} or not isinstance(result['content'], str) or not result['content'].strip():
                raise ValueError('Provider must return content only')
            if role == 'commit_message' and any(len(line) > 80 for line in result['content'].splitlines()):
                raise ValueError('Every commit message line must be at most 80 characters')
            validate(handoff, role)
            return {'status': 'draft', 'content': result['content'], 'publication_authorized': False}
        except (ValueError, TypeError) as exc:
            if attempt:
                raise ValueError('Draft provider failed after one correction: ' + str(exc)) from exc
            prompt += '\nCorrect the prior response: ' + str(exc) + '\nPrior response: ' + raw[:12000]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--role', choices=ROLES, required=True)
    parser.add_argument('--cwd', default='.')
    parser.add_argument('--pr')
    parser.add_argument('--handoff')
    args = parser.parse_args()
    row = configuration(args.cwd, args.pr)[args.role]
    if row['binding'] == 'builtin':
        result = {'status': 'builtin', 'source': row['source']}
    elif not args.handoff:
        result = {'status': 'needs_evidence', **row}
    else:
        result = {**draft(row['binding'], args.role, args.handoff, args.cwd), 'source': row['source']}
    print(json.dumps(result, ensure_ascii=False))


if __name__ == '__main__':
    try:
        main()
    except Exception as exc:
        print(str(exc), file=sys.stderr)
        sys.exit(1)
