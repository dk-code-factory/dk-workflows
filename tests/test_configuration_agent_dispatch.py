"""Offline dispatch boundary fixtures; actual native adherence remains unmeasured."""
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]/'runtime'))
from config_requests import Requests
from configuration_agent_dispatch import dispatch


class Host:
    def __init__(self, available=True):
        self.available = available
        self.calls = []

    def spawn(self, handoff):
        self.calls.append(('spawn', handoff))
        return {'agent_id': 'fixture-native-agent', 'request_id': handoff.get('request_id')}

    def resume(self, agent, handoff):
        self.calls.append(('resume', agent, handoff))
        return {'agent_id': agent, 'request_id': handoff.get('request_id')}


def test_native_handoff_is_configuration_only_and_resumes_same_request(tmp_path):
    checkout = tmp_path/'checkout'
    checkout.mkdir()
    store = Requests()
    result = store.start('Use a concrete timeout.', {'checkout': str(checkout)})
    handoff = result['handoff']
    assert handoff['responsibility'] == 'configuration-only'
    assert Path(handoff['contract']).name == 'configuration-agent.md'
    host = Host()
    execution = dispatch(handoff, host)
    store.clarify(result['id'], 'Use repository scope.')
    resumed = Requests().resume(result['id'])
    assert resumed['handoff'] == handoff
    dispatch(resumed['handoff'], host, existing_agent=execution['agent_id'])
    assert [call[0] for call in host.calls] == ['spawn', 'resume']
    assert dispatch(handoff, Host(False)) == {'status': 'blocked', 'request_id': result['id']}
    # The production prompt owns actual native dispatch, not this fixture/helper.
    skill = (Path(__file__).resolve().parents[1]/'codex/skills/dk-config/SKILL.md').read_text()
    assert 'fresh supported native agent' in skill and 'Missing native delegation blocks' in skill


def test_explicit_inspection_uses_transient_native_assignment_without_history():
    store = Requests()
    host = Host()
    handoff = {'responsibility':'configuration-only', 'mode':'inspect',
               'contract':str(Path(__file__).resolve().parents[1]/'docs/configuration-agent.md'),
               'ask':'Explain the current timeout and its source.'}
    assert dispatch(handoff, host)['request_id'] is None
    assert host.calls[0][0] == 'spawn'
    assert not store.root.exists()
    assert dispatch(handoff, Host(False))['status'] == 'blocked'
