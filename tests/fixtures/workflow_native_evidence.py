"""Read native command evidence for opt-in quality audits; never execute it.

Observations corroborate reported drivers. They do not decide finding quality or
selected-stage completeness. A multi-command shell's exit belongs to the whole
script; individual status needs direct argv or one plain shell command.
"""
import hashlib
import json
from pathlib import Path
import shlex

import native_cost_observations as observations
import workflow_cost


MARKER = 'NATIVE_REVIEW_DRIVERS_JSON='


def plain_argv(line):
    try:
        return shlex.split(line)
    except ValueError:
        return []


def inspect_thread(rows, thread, turns):
    reported, commands, malformed = {}, [], []
    for number, row in enumerate(rows, 1):
        payload = row['payload']
        if row['type'] != 'event_msg' or payload.get('turn_id') not in turns:
            continue
        if payload.get('type') == 'item_completed' and payload.get('thread_id') == thread:
            item = payload['item']
            if item.get('type') == 'CommandExecution':
                commands.append((number, item))
        if payload.get('type') not in ('task_complete', 'task_completed'):
            continue
        for line in payload.get('last_agent_message', '').splitlines():
            if not line.startswith(MARKER):
                continue
            try:
                drivers = json.loads(line[len(MARKER):])
                if isinstance(drivers, list) and drivers and all(isinstance(v, str) for v in drivers):
                    drivers = [drivers]
                observations.require(isinstance(drivers, list), 'Invalid driver marker')
                for driver in drivers:
                    value = driver if isinstance(driver, dict) else {'argv': driver}
                    argv = value['argv']
                    observations.require(isinstance(argv, list) and argv and
                        all(isinstance(arg, str) and arg for arg in argv), 'Invalid driver argv')
                    key = tuple(argv)
                    previous = reported.get(key, {})
                    reported[key] = {'argv': argv, 'reported_state': value.get('state'),
                        'reported_exit_code': value.get('exit_code'),
                        'marker_lines': [*previous.get('marker_lines', []), number]}
            except (ValueError, TypeError, KeyError):
                malformed.append(number)
    for argv, driver in reported.items():
        evidence = []
        for number, item in commands:
            command = item.get('command', [])
            direct = tuple(command) == argv
            lines = command[-1].splitlines() if len(command) == 3 and command[1] in ('-c', '-lc') else []
            lines = [line.strip() for line in lines if line.strip()]
            matching = [index for index, line in enumerate(lines) if tuple(plain_argv(line)) == argv]
            if not direct and not matching:
                continue
            plain = len(lines) == 1 and not any(char in lines[0] for char in
                                               ';|&<>`$(){}\\*?[]~')
            terminal = direct or plain and len(lines)-1 in matching
            evidence.append({'line': number, 'item_id': item.get('id'),
                'command_contains_reported_argv': True,
                'host_command_status': item.get('status'), 'host_command_exit_code': item.get('exit_code'),
                'individual_exit_code': item.get('exit_code') if terminal and item.get('status') == 'completed' else None,
                'individual_exit_proven': terminal and item.get('status') == 'completed'})
        driver['command_observations'] = evidence
    return {'reported_drivers': list(reported.values()), 'malformed_marker_lines': malformed,
            'scope': 'Original own-turn markers and host command observations; no quality verdict.'}


def collect(database, roots, accounting):
    observations.require(accounting.get('telemetry_complete') is True,
                         'Complete original native accounting required')
    graph = workflow_cost.host_graph(database, roots)
    observations.require(workflow_cost.digest(graph) == accounting['graph_sha256'],
                         'Native graph differs from original accounting')
    result = {}
    for thread, node in graph.items():
        path = Path(node['rollout'])
        raw = path.read_bytes()
        observations.require(hashlib.sha256(raw).hexdigest() ==
                             accounting['threads'][thread]['raw_rollout_sha256'],
                             'Native evidence changed after accounting')
        turns = {turn['turn_id'] for turn in accounting['threads'][thread]['turns']}
        value = inspect_thread([json.loads(line) for line in raw.splitlines()], thread, turns)
        if value['reported_drivers'] or value['malformed_marker_lines']:
            result[thread] = value
    observations.require(graph == workflow_cost.host_graph(database, roots),
                         'Native graph changed during quality evidence collection')
    return result
