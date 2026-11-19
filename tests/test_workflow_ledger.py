"""Public ledgers reproduce prices without disclosing private native traces."""
from copy import deepcopy
import json
from pathlib import Path
import subprocess
import sys

import pytest

from test_workflow_cost import host
import workflow_cost
import workflow_ledger


def ledger(tmp_path):
    database = host(tmp_path)
    rows = workflow_ledger.export(database, {'initial': 'workflow-initial',
        'resume': 'workflow-recovery', 'orchestration': 'orchestration'},
        source_revision='a'*40, reader_revision='b'*40)
    return database, rows


def test_public_ledger_prices_every_unique_response_and_keeps_overhead_separate(tmp_path):
    _, rows = ledger(tmp_path)
    result = workflow_ledger.verify(rows)
    assert result['pricing_verified'] and result['thread_count'] == 4
    assert result['response_count'] == 12 and result['workflow_cost_usd'] == '0.0036'
    assert rows[0]['buckets']['orchestration']['cost'] == '0.0009'
    text = json.dumps(rows)
    assert str(tmp_path) not in text
    assert 'rollout_path' not in text and 'last_agent_message' not in text
    assert '"initial"' not in text and '"reviewer-response-1"' not in text
    assert 'authenticity' in result['scope'] and 'quality' in result['scope']


@pytest.mark.parametrize('mutation', ['duplicate-thread', 'missing-thread', 'cycle',
    'missing-parent', 'duplicate-response', 'missing-response', 'wrong-price',
    'cache-write', 'model', 'rate-card', 'missing-turns', 'descendant-bucket'])
def test_ledger_rejects_incomplete_or_conflicting_accounting(tmp_path, mutation):
    _, rows = ledger(tmp_path)
    row = rows[1]
    child = next(value for value in rows[1:] if value['parent'] is not None)
    turn = row['turns'][0]
    if mutation == 'duplicate-thread': rows.append(deepcopy(row))
    elif mutation == 'missing-thread': rows.remove(child)
    elif mutation == 'cycle': child['parent'] = child['thread']
    elif mutation == 'missing-parent': child['parent'] = 'd'*64
    elif mutation == 'duplicate-response':
        child_turn = child['turns'][0]
        child_turn['responses'][0][0] = turn['responses'][0][0]
        child_turn['inventory_sha256'] = workflow_cost.digest(
            [response[0] for response in child_turn['responses']])
    elif mutation == 'missing-response':
        turn['responses'].pop()
        turn['inventory_sha256'] = workflow_cost.digest([value[0] for value in turn['responses']])
    elif mutation == 'wrong-price': rows[0]['workflow_cost_usd'] = '0'
    elif mutation == 'cache-write': turn['responses'][0][3] = 1
    elif mutation == 'model': turn['settings']['model'] = 'unknown'
    elif mutation == 'rate-card':
        rows[0]['rate_card'] = deepcopy(rows[0]['rate_card'])
        rows[0]['rate_card']['per_million_usd']['input'] = '1'
    elif mutation == 'missing-turns': child['turns'] = []
    elif mutation == 'descendant-bucket': child['bucket'] = 'review'
    with pytest.raises(ValueError):
        workflow_ledger.verify(rows)


def test_public_pricing_retains_context_surcharge_and_counts_reasoning_once(tmp_path):
    _, rows = ledger(tmp_path)
    # This is an arithmetic fixture, not a claim about original host authenticity.
    rows[1]['turns'][0]['responses'][0][1:] = [272001, 200000, 0, 40, 20]
    rows[0]['buckets']['workflow-initial']['cost'] = '0.331004'
    rows[0]['workflow_cost_usd'] = '0.331904'
    assert workflow_ledger.verify(rows)['workflow_cost_usd'] == '0.331904'


def test_fresh_offline_process_verifies_original_ledger_without_raw_host_access(tmp_path):
    _, rows = ledger(tmp_path)
    path = tmp_path/'public.jsonl'
    path.write_text(''.join(json.dumps(row)+'\n' for row in rows))
    before = path.read_bytes()
    result = subprocess.run([sys.executable, str(Path(workflow_ledger.__file__)),
                             'verify', str(path)], capture_output=True, text=True, timeout=10)
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout)['response_count'] == 12
    assert path.read_bytes() == before


def test_export_refuses_an_existing_report_before_reading_changed_inputs(tmp_path):
    path = tmp_path/'original.jsonl'
    path.write_text('Preserved original result.\n')
    before = path.read_bytes()
    result = subprocess.run([sys.executable, str(Path(workflow_ledger.__file__)), 'export',
        '--database', str(tmp_path/'unavailable.sqlite'), '--roots', str(tmp_path/'missing.json'),
        '--source-revision', 'a'*40, '--reader-revision', 'b'*40, '--output', str(path)],
        capture_output=True, text=True, timeout=10)
    assert result.returncode != 0 and 'Refusing to overwrite' in result.stderr
    assert path.read_bytes() == before


def test_export_rejects_raw_evidence_that_changed_after_collection(tmp_path, monkeypatch):
    database = host(tmp_path)
    original = workflow_ledger.workflow_cost.collect

    def collect(*args, **kwargs):
        result = original(*args, **kwargs)
        with (tmp_path/'initial.jsonl').open('a') as stream:
            stream.write(json.dumps({'type': 'response_item', 'payload': {}})+'\n')
        return result

    monkeypatch.setattr(workflow_ledger.workflow_cost, 'collect', collect)
    with pytest.raises(ValueError, match='Raw trace changed'):
        workflow_ledger.export(database, {'initial': 'workflow-initial'},
                               source_revision='a'*40, reader_revision='b'*40)
