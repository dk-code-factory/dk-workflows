import importlib.util
import json
from pathlib import Path
import sys

import pytest

from shared.private_state import write


spec = importlib.util.spec_from_file_location(
    'review_result_state', Path(__file__).parents[1] / 'scripts/run_state.py')
state = importlib.util.module_from_spec(spec)
spec.loader.exec_module(state)


@pytest.mark.parametrize('output', [
    [],
    {},
    {'review_artifact': []},
    {'review_artifact': {'sha256': 'a' * 64}},
    {'review_artifact': {'path': 'result.json'}},
    {'review_artifact': {'path': [], 'sha256': 'a' * 64}},
    {'review_artifact': {'path': 'result.json', 'sha256': None}},
])
def test_malformed_review_result_cli_has_diagnostic_without_mutating_record(
        tmp_path, monkeypatch, capsys, output):
    head = 'a' * 40
    # Preparation gating is independent of parsing external runner output.
    monkeypatch.setattr(state, 'work_gate', lambda *args: {'preparation_id': 'ready'})
    record = {}
    state.start_run(record)
    state.next_cycle(record, head)
    record_path = tmp_path / 'pr-1.json'
    state.review_session(record, head, record_path)
    record['review_results'] = [{'path': 'previous immutable result', 'sha256': 'b' * 64}]
    write(record_path, record)
    original = record_path.read_bytes()
    output_path = tmp_path / 'runner-output.json'
    output_path.write_text(json.dumps(output))
    monkeypatch.setattr(state, 'root', lambda repo: tmp_path)
    monkeypatch.setattr(sys, 'argv', [
        'run_state.py', '--repo', 'github.com/o/r', '--pr', '1',
        'review-result', '--head', head, '--file', str(output_path),
    ])

    with pytest.raises(SystemExit) as exit_info:
        state.main()

    assert exit_info.value.code == 2
    captured = capsys.readouterr()
    assert captured.out == ''
    assert captured.err.startswith('run_state: ')
    assert 'review_artifact' in captured.err
    assert 'Traceback' not in captured.err
    assert record_path.read_bytes() == original
