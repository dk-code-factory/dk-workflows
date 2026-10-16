#!/usr/bin/env python3
"""Check iteration tools and private configuration before any PR mutation."""
import sys
from pathlib import Path
sys.path.insert(0, str(next(p for p in Path(__file__).resolve().parents if (p / 'shared').is_dir())))
import argparse
import json
from pathlib import Path
import shutil
import subprocess
import sys

ROOT = next(p for p in Path(__file__).resolve().parents if (p / 'shared').is_dir())


def configuration(cwd, pr):
    result = {'ready': False, 'blockers': [], 'reviewers': None, 'completion': None}
    missing = [name for name in ('git', 'gh') if not shutil.which(name)]
    if missing:
        result['blockers'].append({
            'check': 'tools',
            'message': 'Required executables are unavailable on PATH: ' + ', '.join(missing),
            'next': 'Use the installed tool directory on PATH, or install the missing tools, then rerun preflight.',
        })
        return result
    for name, script, command in (
            ('reviewers', 'dk-config/scripts/repo_rules.py', ['reviewers', 'list']),
            ('diagnostics', 'dk-config/scripts/repo_rules.py', ['diagnose']),
            ('completion', 'dk-pr-iter/scripts/completion.py', ['show'])):
        try:
            proc = subprocess.run([sys.executable, str(ROOT / script), '--cwd', str(cwd),
                                   '--pr', pr, *command], text=True, capture_output=True,
                                  timeout=60)
            if proc.returncode:
                raise ValueError(proc.stderr.strip() or proc.stdout.strip() or 'configuration lookup failed')
            value = json.loads(proc.stdout)
            if not isinstance(value, dict):
                raise ValueError('configuration response must be an object')
            if name == 'reviewers':
                if value.get('configured') is not True or not value.get('reviewers'):
                    raise ValueError('No PR reviewers are configured. Select installed reviewer skills with dk-config reviewers set SKILL.')
            elif name == 'diagnostics':
                missing = [entry for entry in value['reviewers'] if entry['missing']]
                missing += [entry for role, entry in value['drafting'].items() if role in ('commit_message', 'pr_description') and entry['missing']]
                if missing: raise ValueError(json.dumps(missing))
            elif not isinstance(value.get('prompt'), str) or not value['prompt'].strip():
                raise ValueError('completion prompt is missing or empty')
            result[name] = value
        except (OSError, ValueError, subprocess.TimeoutExpired) as exc:
            result['blockers'].append({'check': name, 'message': str(exc),
                                       'next': 'Resolve this configuration error and rerun preflight; do not mutate the PR.'})
    result['ready'] = not result['blockers']
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--cwd', default='.')
    parser.add_argument('--pr', required=True, help='Explicit PR URL')
    args = parser.parse_args()
    result = configuration(Path(args.cwd).resolve(), args.pr)
    print(json.dumps(result, ensure_ascii=False))
    return 0 if result['ready'] else 1


if __name__ == '__main__':
    sys.exit(main())
