"""Offline native coordination fixture; never launches or drives an agent.

Freezes real helpers and supplies sixteen bounded stand-in duties. Native agents
choose preparation, evidence reads, recording and recovery. Independent verification
checks complete original results and fresh-session reuse without trusting reports.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

import dollar_exercise

REPO, PR = dollar_exercise.REPO, dollar_exercise.PR
LABELS = ('feedback', 'decisions', 'feedback-fix', 'feedback-publication', 'partition',
          'description', 'discovery', 'finding-assessment', 'finding-publication',
          'commit-cleanup', 'final-decisions', 'final-description', 'replies', 'ci',
          'final-publication')


def save(path, value):
    dollar_exercise.save(path, value)


def initialize(root, source):
    subprocess.run([sys.executable, str(Path(dollar_exercise.__file__)), '--root', str(root),
                    '--source', str(source), '--experiment', '1', 'init'], check=True,
                   stdout=subprocess.DEVNULL)
    for name in ('AGENTS.md', 'DESIGN.md', 'README.md'):
        shutil.copy2(source/name, root/'frozen'/name)
    scenario = json.loads((root/'inputs/scenario.json').read_text())
    instructions = root/'inputs/instructions.txt'
    instructions.write_text('Assess the complete offline fixture. Keep VALUE public and equal to 2.\n'
                            'F1 is explicitly accepted at '+scenario['source']+'.\n'
                            'No network, remote writes, native descendants or edits to frozen inputs.\n')
    duties = [dict(instructions='Assess offline '+label+' against inputs/evidence.json. '
                   'Keep the exact F1 acceptance and source. No remote writes.', options={}, required=True)
              for label in LABELS]
    options = {'tasks': duties[:9], 'finalizers': duties[9:]}
    save(root/'inputs/evidence.json', {'head': scenario['head'], 'value': 2, 'finding': 'F1',
        'disposition': 'explicitly accepted', 'acceptance': scenario['acceptance'],
        'source': scenario['source'], 'publication': 'reconciled fixture no-op',
        'ci': 'offline exact-head stand-in; not real GitHub evidence'})
    definition = json.loads((root/'inputs/definition.json').read_text())
    definition['skill_identity'] = 'dk-workflows:dk-pr-fix'
    definition['resources'] = {'skill': str(root/'inputs/skill.md')}
    definition['inputs']['evidence'] = str(root/'inputs/evidence.json')
    tasks = []
    for index, label in enumerate(LABELS):
        fields = {**definition, 'responsibility': 'stage-'+str(index),
                  'assigned_instructions': duties[index]['instructions']}
        if label == 'commit-cleanup':
            fields['git'] = {**fields['git'], 'history': True}
        if label in ('ci', 'final-publication'):
            fields['cacheable'] = False
        tasks.append({'label': label, 'descriptor': {'kind': 'skill',
            'skill': 'dk-workflows:dk-pr-fix', 'selection': 'tasks' if index < 9 else 'finalizers',
            'index': index if index < 9 else index-9, 'options': options, 'definition': fields}})
    completion = {**definition, 'assigned_instructions':
        'Verify all selected offline fixture duties, accepted F1 and retained evidence. No remote writes.'}
    completion.pop('responsibility')
    tasks.append({'label': 'completion', 'descriptor': {'kind': 'completion',
                  'options': options, 'definition': completion}})
    save(root/'inputs/tasks.json', tasks)
    scenario['assignment'] = ('Coordinate every task in inputs/tasks.json in order under run api-cost. '
        'Do not pass cycle flags for these instruction-only duties. They stand in for domain skills; they do not claim native '
        'reviewer execution or real GitHub CI/publication. Read the prepared assignment and checklist '
        'and actual evidence for each duty. Preserve exact F1 acceptance/source and original outputs. '
        'Use the frozen managed prepare/finish interface, honoring each returned action. Use available '
        'compact presentation when an assignment is already in context; retrieve full assignments '
        'when needed. Supply source-backed checklist outcomes only after doing the assigned work. '
        'Never synthesize native observations. Complete the selected fixture plan and save an accurate '
        'artifacts/phase-1.json assessment with complete, finding, acceptance, source and blockers. '
        'Do not run the independent verifier. A later fresh coordinator resumes this same state.')
    scenario['resume_assignment'] = ('Resume run api-cost with no inherited conversation. Read current '
        'guidance, saved evidence and necessary original results through supported helpers. Demonstrate '
        'reuse of every unchanged cacheable task without repeating analysis or allocating another '
        'cycle. Refresh the two noncacheable selected duties and coordinator completion against current '
        'evidence. Preserve all original findings, acceptance/source, completed siblings, and counters. '
        'Do not change any frozen input or reset state. Save an accurate artifacts/phase-2.json '
        'assessment with complete, finding, acceptance, source and blockers. No network or descendants.')
    save(root/'inputs/scenario.json', scenario)
    dollar_exercise.freeze(root)
    return scenario


def verify(root):
    for key, folder in [('XDG_STATE_HOME', 'state'), ('XDG_CONFIG_HOME', 'config'), ('XDG_DATA_HOME', 'data')]:
        os.environ[key] = str(root/folder)
    sys.path.insert(0, str(root/'frozen/runtime'))
    from state import read
    from tasks import Tasks
    tasks = Tasks(REPO, PR, checkout=root/'checkout')
    status = tasks.resume(run='api-cost')
    manifest = read(tasks.store.manifest)
    scenario = json.loads((root/'inputs/scenario.json').read_text())
    frozen = json.loads((root/'freeze.json').read_text())
    problems = []
    for name, expected in frozen['files'].items():
        path = root/name
        if not path.exists() or hashlib.sha256(path.read_bytes()).hexdigest() != expected:
            problems.append('Changed frozen resource: '+name)
    inputs = {str(p.relative_to(root)): hashlib.sha256(p.read_bytes().replace(str(root).encode(), b'ARM')).hexdigest()
              for p in sorted((root/'inputs').rglob('*')) if p.is_file()}
    if hashlib.sha256(json.dumps(inputs, sort_keys=True).encode()).hexdigest() != frozen['input_hash']:
        problems.append('Changed governing inputs')
    if status['unfinished'] or len(status['tasks']) != 16 or not all(t['coverage_complete'] for t in status['tasks']):
        problems.append('Incomplete selected workflow or unresolved claims')
    attempts = []
    for entry in manifest['entries'].values():
        spec = entry['components']
        for attempt in entry['attempts']:
            # Refreshing a noncacheable duty must retain its original output too.
            filename = attempt.get('result', '')
            original, damaged = tasks.store._load_saved({'key': entry['key'], 'result': {
                'file': filename, 'hash': Path(filename).stem,
                'fingerprint': attempt['fingerprint']}})
            if damaged:
                problems.append('Missing or damaged original attempt evidence')
        saved, problem = tasks.store._load_saved(entry)
        if problem:
            problems.append('Missing original task evidence')
            continue
        cacheable = saved['cacheable']
        expected = 1 if cacheable else 2
        if len(entry['attempts']) != expected:
            problems.append('Repeated cached analysis or missing fresh work: '+entry['key'])
        result = tasks.result(entry['key'], 'normalized')['normalized']
        # Native reports may retain the finding as a normal findings[] item.
        # The assignment fixes its meaning and exact bytes, not its JSON nesting.
        outcomes = ([result] if result.get('finding') == 'F1' else []) + [
            item for item in result.get('findings', [])
            if isinstance(item, dict) and item.get('id') == 'F1']
        if not outcomes or any(item.get('acceptance') != scenario['acceptance'] or
                               item.get('source') != scenario['source'] for item in outcomes):
            problems.append('Lost original finding or exact sourced acceptance')
        attempts.append({'task': spec['task']['id'], 'attempts': len(entry['attempts']),
                         'cacheable': cacheable})
    run = manifest['runs'].get('api-cost', {})
    if any(run.get(field) for field in ('cycles', 'retries', 'ci_repairs', 'ci_reruns')):
        problems.append('Instruction-only fixture work consumed reviewer or CI budgets')
    for phase in (1, 2):
        path = root/'artifacts'/('phase-'+str(phase)+'.json')
        report = json.loads(path.read_text()) if path.exists() else {}
        if (report.get('complete') is not True or report.get('blockers') or report.get('finding') != 'F1'
                or report.get('acceptance') != scenario['acceptance'] or report.get('source') != scenario['source']):
            problems.append('Missing or inaccurate phase assessment: '+str(phase))
    return {'passed': not problems, 'problems': problems, 'tasks': attempts,
            'scope': 'Complete selected instruction-only fixture plan and fresh-session resume',
            'limitation': 'Offline domain stand-ins do not establish production reviewer recall or GitHub behavior'}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', required=True, type=Path)
    sub = parser.add_subparsers(dest='command', required=True)
    p = sub.add_parser('init')
    p.add_argument('--source', required=True, type=Path)
    sub.add_parser('verify')
    args = parser.parse_args()
    result = initialize(args.root.resolve(), args.source.resolve()) if args.command == 'init' else verify(args.root.resolve())
    print(json.dumps(result, sort_keys=True))


if __name__ == '__main__':
    main()
