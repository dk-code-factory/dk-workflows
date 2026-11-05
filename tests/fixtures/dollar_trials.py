"""Opt-in native trial driver. Never imported by offline suites or production.

Freeze first; each completed arm is immutable and never rerun. Independent SSE
response creation/completion inventories corroborate persisted usage records and
exec turn totals. Full prompts, SSE and rollout references remain private.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import sqlite3
import subprocess
import sys
import time

import dollar_exercise as fixture
import dollar_usage as dollars
import native_usage

ORDERS = [['baseline', 'candidate'], ['candidate', 'baseline'], ['baseline', 'candidate']]
ASSIGNMENT = '''You are a fresh native coordinator in a controlled offline workflow experiment.
Work exclusively within ARM. Read inputs/scenario.json and complete its assignment using real frozen production helpers under frozen/. Discover applicable guidance in frozen/docs/task-state.md and helper help/source as needed. Choose your own operations and write wrappers under artifacts/ if useful. Do not change frozen/, inputs/, initial-manifest.json, interface.py or freeze.json. Do not launch agents, access a network, change personal settings, use a different runtime, or write outside ARM. Local fixture Git and gh transports are authorized; no real publication is authorized.
Your shell environment sets isolated XDG state/config/data and PATH for local gh when present. Invoke helpers normally with python3. Baseline capabilities may require your own bookkeeping wrapper; candidate capabilities are available through helper help/source and guidance. Preserve complete original findings and exact acceptance text/source, scoped preference inputs, active claim ownership and retained counters. Fixture initialization is not native work. Do not infer success from command exit alone.
The complete field means the bounded assigned exercise is complete; report overall workflow coverage/readiness separately. Write an accurate JSON terminal assessment to artifacts/assessment.json with complete, findings, blockers, operations/evidence references, acceptance and source, reuse and coverage observations. Then give a short terminal assessment. Do not run the fixture verifier: the evaluator independently verifies your saved outcomes.
'''


def sha(data):
    return hashlib.sha256(data).hexdigest()


def save(path, data):
    path.write_text(json.dumps(data, indent=2, sort_keys=True)+'\n')


def rows(path):
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


def collect(root, exit_code):
    """Fail closed if any independent inventory, final response or total is absent."""
    events = rows(root/'exec.jsonl')
    thread = next(e['thread_id'] for e in events if e['type'] == 'thread.started')
    completed = [e for e in events if e['type'] == 'turn.completed']
    if exit_code or len(completed) != 1:
        raise ValueError('Native host did not complete exactly one turn')
    db = sqlite3.connect('file:'+str(Path.home()/'.codex/state_5.sqlite')+'?mode=ro', uri=True)
    db.row_factory = sqlite3.Row
    record = dict(db.execute('select * from threads where id=?', (thread,)).fetchone())
    descendants = db.execute('WITH RECURSIVE children(id) AS (SELECT child_thread_id FROM thread_spawn_edges WHERE parent_thread_id=? UNION ALL SELECT child_thread_id FROM thread_spawn_edges JOIN children ON parent_thread_id=children.id) SELECT id FROM children', (thread,)).fetchall()
    if descendants:
        raise ValueError('Unexpected descendants require independent turn inventories')
    rollout = Path(record['rollout_path'])
    persisted = rows(rollout)
    starts = [r['payload'] for r in persisted if r['type'] == 'event_msg' and r['payload'].get('type') == 'task_started']
    ends = [r['payload'] for r in persisted if r['type'] == 'event_msg' and r['payload'].get('type') in ('task_complete', 'task_completed')]
    if len(starts) != 1 or len(ends) != 1:
        raise ValueError('Incomplete independent host turn inventory')
    created, responses = [], []
    for line in (root/'native.stderr').read_text().splitlines():
        if 'SSE event: ' not in line:
            continue
        raw = line.split('SSE event: ',1)[1]
        try: event = json.loads(raw)
        except ValueError: continue
        if event.get('type') == 'response.created':
            created.append(event['response']['id'])
        if event.get('type') == 'response.completed':
            response = event['response']
            if response.get('model') != 'gpt-6.1-sol' or response.get('service_tier') != 'default':
                raise ValueError('Response controls differ from frozen model/service tier')
            responses.append(response['id'])
    created = list(dict.fromkeys(created))
    responses = list(dict.fromkeys(responses))
    if not responses or created != responses:
        raise ValueError('Incomplete independent SSE response/terminal inventory')
    total = completed[0]['usage']
    total['total_tokens'] = total['input_tokens'] + total['output_tokens']
    settings = [r['payload'] for r in persisted if r['type'] == 'turn_context']
    if len(settings) != 1:
        raise ValueError('Unpinned execution context')
    context = settings[0]
    manifest = {'pins': {'source': 'frozen-runtime', 'guidance': 'frozen-guidance', 'scenario': 'frozen-scenario'},
        'inventory_complete': True,
        'participants': [{'thread_id': thread, 'parent_thread_id': None,
            'model': context['model'], 'effort': context.get('effort'), 'service_tier': 'default',
            'turns': [{'turn_id': starts[0]['turn_id'], 'complete': True, 'response_ids': responses,
                       'terminal_response_id': responses[-1], 'expected_usage': total}]}],
        'coordination_call_ids': []}
    save(root/'measurement-manifest.json', manifest)
    summary = native_usage.summarize([rollout], manifest)
    if summary['tokens']['cache_write_input_tokens']:
        raise ValueError('Cache-write TTL is not independently observed')
    applied = [r['payload']['thread_settings'] for r in persisted if
               r['type'] == 'event_msg' and r['payload'].get('type') == 'thread_settings_applied']
    if not applied or any(s.get('service_tier') != 'default' for s in applied):
        raise ValueError('Service tier was not independently pinned')
    freeze = json.loads((root/'freeze.json').read_text())
    summary.update(response_ids=responses, rate_card=dollars.RATE_CARD,
                   api_equivalent_cost_usd=summary['estimated_api_cost_usd'],
                   host_thread_id=thread, host_exit_code=exit_code,
                   independent_response_inventory='SSE response.created and response.completed',
                   independent_turn_total='exec turn.completed',
                   descendants_observed=len(descendants),
                   raw_rollout_sha256=sha(rollout.read_bytes()),
                   terminal_assessment_sha256=sha((root/'last-message.txt').read_bytes()))
    summary['pins'] = {k:freeze[k] for k in ('runtime','guidance','resources')}
    summary['pins'].update(assignment=sha(ASSIGNMENT.encode()), scenario=freeze['input_hash'],
                           conditions=sha((root/'conditions.json').read_bytes()))
    save(root/'usage.json', summary)
    save(root/'private-rollout-reference.json', {'path':str(rollout)})
    return summary


def observed_partial(root, exit_code, error):
    """Retain observed spending even when completeness cannot be established."""
    from decimal import Decimal
    events = rows(root/'exec.jsonl')
    thread = next((e.get('thread_id') for e in events if e.get('type') == 'thread.started'), None)
    paths = list((Path.home()/'.codex/sessions').glob('*/*/*/*'+str(thread)+'*.jsonl'))
    totals = {key: 0 for key in native_usage.TOKEN_FIELDS}
    cost = Decimal(0)
    records = native_usage.read_records(paths) if paths else {}
    selected = [r for r in records.values() if r['thread_id'] == thread]
    for record in selected:
        for key in totals:
            totals[key] += record['usage'][key]
        cost += dollars.response_cost(record['usage'], service_tier='default')
    if paths:
        save(root/'private-rollout-reference.json', {'path': str(paths[0])})
    return {'telemetry_complete': False, 'error': error, 'host_exit_code': exit_code,
            'host_thread_id': thread, 'observed_tokens': totals,
            'observed_response_count': len(selected), 'observed_api_equivalent_cost_usd': str(cost),
            'host_terminal_usage': [e['usage'] for e in events if e['type'] == 'turn.completed'],
            'rate_card': dollars.RATE_CARD,
            'limitation': 'Observed responses only; complete scope and terminal-response inventory unverified'}


def launch(root):
    if (root/'exec.jsonl').exists():
        raise ValueError('Never rerun an initiated native arm')
    env = {**os.environ, 'XDG_STATE_HOME':str(root/'state'), 'XDG_CONFIG_HOME':str(root/'config'),
           'XDG_DATA_HOME':str(root/'data'), 'PATH':str(root/'bin')+os.pathsep+os.environ['PATH'],
           'RUST_LOG':'warn,codex_api::sse::responses=trace'}
    command = ['codex','exec','-c','model_provider="dollar-sse"','-c',
               'model_providers.dollar-sse={name="OpenAI",wire_api="responses",requires_openai_auth=true,supports_websockets=false,include_internal_metadata=true}',
               '--dangerously-bypass-approvals-and-sandbox','--skip-git-repo-check',
               '--color','never','--json','-C',str(root),'-o',str(root/'last-message.txt'),'-']
    config = Path.home()/'.codex/config.toml'
    conditions = {'codex_version':subprocess.check_output(['codex','--version'],text=True).strip(),
                  'config_sha256':sha(config.read_bytes()),
                  'model':'gpt-6.1-sol','effort':'high','service_tier':'default',
                  'transport':'SSE forced through provider capability with private response tracing', 'personal_settings_changed':False}
    save(root/'conditions.json', conditions)
    with (root/'exec.jsonl').open('w') as out, (root/'native.stderr').open('w') as err:
        result = subprocess.run(command, input=ASSIGNMENT.replace('ARM',str(root)),text=True,
                                stdout=out,stderr=err,env=env)
    save(root/'host-outcome.json',{'exit_code':result.returncode})
    try: summary = collect(root, result.returncode)
    except (ValueError, KeyError, OSError, StopIteration, TypeError) as error:
        summary = observed_partial(root, result.returncode, str(error))
        save(root/'usage.json', summary)
    return summary


def main():
    os.umask(0o077)
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--root',type=Path,required=True)
    p.add_argument('--baseline',type=Path,required=True)
    p.add_argument('--candidate',type=Path,required=True)
    p.add_argument('--experiment',type=int,required=True)
    p.add_argument('--bundle',help='Explicit accepted proposal numbers for combined validation')
    a=p.parse_args()
    a.root.mkdir(parents=True,exist_ok=True)
    # All six inputs freeze before the first assignment, never between trials.
    for i, order in enumerate(ORDERS,1):
        for arm in order:
            root=a.root/(str(i)+'-'+arm)
            if not root.exists():
                subprocess.run([sys.executable, str(Path(fixture.__file__)), '--root',str(root),
                    '--source',str(a.baseline if arm=='baseline' else a.candidate),
                    '--experiment',str(a.experiment), *(['--bundle',a.bundle] if a.bundle else []),
                    'init'],check=True,stdout=subprocess.DEVNULL)
    for i, order in enumerate(ORDERS,1):
        for arm in order:
            root=a.root/(str(i)+'-'+arm)
            summary=launch(root)
            print(json.dumps({'pair':i,'arm':arm,'usage':summary}),flush=True)


if __name__=='__main__': main()
