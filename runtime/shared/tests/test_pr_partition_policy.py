import json
from pathlib import Path
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor

import pytest

from shared import pr_partition_policy as policy

PR = 'https://github.com/base/repo/pull/17'
ROOT = Path(__file__).resolve().parents[3]


def test_disable_is_a_durable_pr_specific_decision_with_exact_source():
    source = 'Keep this PR together.\nDo not partition it on later iterations.'
    disabled = policy.configure(PR, 'disable', reason='One coordinated deliverable.', source=source)
    assert disabled['enabled'] is False and disabled['instructions'] is None
    assert disabled['decision']['source'] == source
    assert policy.load(PR.replace('/base/repo/', '/BASE/REPO/')) == disabled
    for other in ('https://github.com/base/repo/pull/18', 'https://github.com/other/repo/pull/17',
                  'https://ghe.example/base/repo/pull/17'):
        assert policy.load(other)['enabled'] is True
    assert (Path(disabled['file']).stat().st_mode & 0o777) == 0o600


def test_enable_and_reset_retain_prior_decisions_and_identical_retries_are_idempotent():
    disabled = policy.configure(PR, 'disable', reason='Keep together.')
    assert policy.configure(PR, 'disable', reason='Keep together.') == disabled
    enabled = policy.configure(PR, 'enable', reason='The integration boundary is now stable.')
    assert enabled['enabled'] is True and len(enabled['history']) == 2
    assert enabled['history'][0]['policy']['enabled'] is False
    assert enabled['fingerprint'] != disabled['fingerprint']
    reset = policy.configure(PR, 'reset', reason='Use normal repository defaults.')
    assert reset['enabled'] is True and reset['instructions'] is None and len(reset['history']) == 3


def test_conditional_instructions_are_exact_and_survive_disable_enable():
    instructions = 'Partition only when both:\n- the schema is stable\n- each piece has an independent test.'
    value = policy.configure(PR, 'instructions', reason='Human partition conditions.', instructions=instructions)
    assert value['instructions'] == instructions and value['decision']['source'] == instructions
    policy.configure(PR, 'disable', reason='Keep this iteration together.')
    enabled = policy.configure(PR, 'enable', reason='Resume using my recorded conditions.')
    assert enabled['instructions'] == instructions
    cleared = policy.configure(PR, 'clear-instructions', reason='Restore the deterministic threshold.')
    assert cleared['enabled'] is True and cleared['instructions'] is None
    assert cleared['history'][0]['policy']['instructions'] == instructions


def test_action_declines_are_audited_without_changing_the_governing_fingerprint():
    original = policy.binding(PR)
    source = '{"decision": "declined", "action_id": "proposal-one"}'
    first = policy.decline(PR, 'split_related', 'proposal-one', reason='Keep these changes together.', source=source)
    assert first['configured'] is False and first['enabled'] is True
    assert policy.binding(PR) == original
    assert first['declines'][0]['reason'] == 'Keep these changes together.'
    assert policy.decline(PR, 'split_related', 'proposal-one', reason='Keep these changes together.', source=source) == first


def test_concurrent_decisions_preserve_all_history():
    def change(number):
        return policy.configure(PR, 'enable' if number % 2 else 'disable', reason='Explicit choice ' + str(number))
    with ThreadPoolExecutor(max_workers=4) as pool:
        list(pool.map(change, range(12)))
    value = policy.load(PR)
    assert len(value['history']) == 12
    assert {entry['reason'] for entry in value['history']} == {'Explicit choice ' + str(n) for n in range(12)}


@pytest.mark.parametrize('url', [None, '17', 'http://github.com/base/repo/pull/17',
    'https://github.com/base/repo/pull/0', 'https://github.com/base/repo/pull/17#discussion_r1',
    'https://user:secret@github.com/base/repo/pull/17', 'https://github.com/base/../pull/17',
    'https://github.com/base/repo/pull/17?x=1'])
def test_policy_requires_unambiguous_base_pr_identity(url):
    with pytest.raises(ValueError, match='full HTTPS'):
        policy.load(url)


@pytest.mark.parametrize('action,kwargs', [('disable', {'reason': ''}), ('enable', {'reason': 'yes', 'source': ''}),
    ('instructions', {'reason': 'condition', 'instructions': '  '}), ('unknown', {'reason': 'invalid'})])
def test_invalid_decision_does_not_create_a_record(action, kwargs):
    with pytest.raises(ValueError):
        policy.configure(PR, action, **kwargs)
    assert not policy.path(PR).exists()


def test_corrupted_history_never_reenables_a_disabled_pr():
    value = policy.configure(PR, 'disable', reason='Keep together.')
    target = Path(value['file'])
    raw = json.loads(target.read_text())
    raw['history'][0]['policy']['enabled'] = True
    target.write_text(json.dumps(raw))
    with pytest.raises(ValueError, match='changed'):
        policy.load(PR)
    before = target.read_bytes()
    with pytest.raises(ValueError):
        policy.configure(PR, 'reset', reason='Restore defaults.')
    assert target.read_bytes() == before


def test_pr_policy_cli_works_without_a_checkout_or_github_reads(tmp_path):
    source = tmp_path / 'human.txt'
    source.write_text('Do not partition this PR, even after new commits.\n')
    command = [sys.executable, str(ROOT / 'scripts/pr_config.py'), '--cwd', str(tmp_path),
               '--pr', PR, 'pr-partition']
    def invoke(*args):
        value = subprocess.run([*command, *args], text=True, capture_output=True)
        assert value.returncode == 0, value.stderr
        return json.loads(value.stdout)
    assert invoke('show')['enabled'] is True
    disabled = invoke('disable', '--reason', 'Keep the whole PR.', '--source-file', str(source))
    assert disabled['decision']['source'] == source.read_text()
    assert invoke('show')['enabled'] is False
    conditional = invoke('instructions', '--text', 'Partition only after integration stabilizes.')
    assert conditional['enabled'] is True and conditional['instructions'].startswith('Partition only')
    invoke('disable', '--reason', 'Temporarily keep together.')
    assert invoke('enable', '--reason', 'Resume the conditions.')['instructions'] == conditional['instructions']
    assert invoke('clear-instructions', '--reason', 'Use the normal threshold.')['instructions'] is None
    assert invoke('reset', '--reason', 'Use inherited defaults.')['enabled'] is True
    missing = subprocess.run([sys.executable, str(ROOT / 'scripts/pr_config.py'),
        '--cwd', str(tmp_path), 'pr-partition', 'disable', '--reason', 'missing identity'], capture_output=True)
    assert missing.returncode != 0


def test_instruction_source_remains_the_exact_condition_source_after_toggle():
    instructions = 'Partition only after the schema stabilizes.'
    initial = policy.configure(PR, 'instructions', reason='A conditional choice.', instructions=instructions,
                               source='Human discussion: keep schema changes together until stable.')
    policy.configure(PR, 'disable', reason='Pause all partitioning.')
    enabled = policy.configure(PR, 'enable', reason='Use my earlier conditions again.')
    assert enabled['decision']['source'] == 'Use my earlier conditions again.'
    assert enabled['instructions_source'] == initial['instructions_source']
    assert enabled['instructions_source']['source'] == 'Human discussion: keep schema changes together until stable.'
