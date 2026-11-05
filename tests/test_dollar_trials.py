"""Independent native inventory collection fails closed without model calls."""
import json
from pathlib import Path
import sqlite3
import sys
import pytest
sys.path.insert(0, str(Path(__file__).parent/'fixtures'))
import dollar_trials as trials
from test_native_usage import record


@pytest.fixture
def collected(tmp_path, monkeypatch):
    root = tmp_path/'arm';root.mkdir()
    home = tmp_path/'home';(home/'.codex').mkdir(parents=True)
    monkeypatch.setattr(Path, 'home', classmethod(lambda cls:home))
    rollout = root/'rollout.jsonl'
    usage = record(writes=0)['payload']['usage']
    events = [{'type':'event_msg','payload':{'type':'task_started','turn_id':'t1'}},
              {'type':'turn_context','payload':{'model':'gpt-6.1-sol','effort':'high'}},
              {'type':'event_msg','payload':{'type':'thread_settings_applied',
                                          'thread_settings':{'service_tier':'default'}}},
              record(writes=0),
              {'type':'event_msg','payload':{'type':'task_complete','turn_id':'t1'}}]
    rollout.write_text(''.join(json.dumps(e)+'\n' for e in events))
    c=sqlite3.connect(home/'.codex/state_5.sqlite')
    c.execute('create table threads (id text, rollout_path text)')
    c.execute('insert into threads values (?,?)',('parent',str(rollout)))
    c.execute('create table thread_spawn_edges (parent_thread_id text, child_thread_id text)')
    c.commit();c.close()
    (root/'exec.jsonl').write_text(json.dumps({'type':'thread.started','thread_id':'parent'})+'\n'+
        json.dumps({'type':'turn.completed','usage':{k:v for k,v in usage.items() if k!='total_tokens'}})+'\n')
    (root/'native.stderr').write_text('SSE event: '+json.dumps({'type':'response.created','response':{'id':'r1','model':'gpt-6.1-sol','service_tier':'default'}})+'\n'+
        'SSE event: '+json.dumps({'type':'response.completed','response':{'id':'r1','model':'gpt-6.1-sol','service_tier':'default'}})+'\n')
    (root/'last-message.txt').write_text('Completed the bounded assignment.')
    (root/'conditions.json').write_text('{}')
    (root/'freeze.json').write_text(json.dumps({k:'pinned' for k in ('runtime','guidance','resources','input_hash')}))
    return root, rollout, home


def test_independent_inventory_matches_deduplicated_response_usage(collected):
    root,rollout,home=collected
    with rollout.open('a') as stream:
        stream.write(json.dumps(record(writes=0))+'\n')
    with (root/'native.stderr').open('a') as stream:
        stream.write('SSE event: '+json.dumps({'type':'response.completed','response':{'id':'r1','model':'gpt-6.1-sol','service_tier':'default'}})+'\n')
    summary=trials.collect(root,0)
    assert summary['telemetry_complete']
    assert summary['response_count']==1
    assert summary['response_ids']==['r1']
    assert summary['descendants_observed']==0


@pytest.mark.parametrize('missing', ['response.created','response.completed'])
def test_missing_independent_inventory_cannot_use_usage_ids_as_fallback(collected,missing):
    root,rollout,home=collected
    path=root/'native.stderr'
    path.write_text('\n'.join(line for line in path.read_text().splitlines() if missing not in line)+'\n')
    with pytest.raises(ValueError,match='inventory'):
        trials.collect(root,0)


def test_missing_descendant_inventory_is_not_charged_as_root_only(collected):
    root,rollout,home=collected
    c=sqlite3.connect(home/'.codex/state_5.sqlite')
    c.execute('insert into thread_spawn_edges values (?,?)',('parent','child'));c.commit();c.close()
    with pytest.raises(ValueError,match='descendants'):
        trials.collect(root,0)


def test_terminal_host_failure_cannot_pass_telemetry(collected):
    root,_,_=collected
    with pytest.raises(ValueError,match='host'):
        trials.collect(root,1)


def test_unknown_cache_write_ttl_blocks_dollar_measurement(collected):
    root,rollout,_=collected
    records=trials.rows(rollout)
    next(r['payload']['usage'] for r in records if r['type']=='token_usage_record')['cache_write_input_tokens']=10
    rollout.write_text(''.join(json.dumps(r)+'\n' for r in records))
    events=trials.rows(root/'exec.jsonl')
    events[-1]['usage']['cache_write_input_tokens']=10
    (root/'exec.jsonl').write_text(''.join(json.dumps(r)+'\n' for r in events))
    with pytest.raises(ValueError,match='TTL'):
        trials.collect(root,0)


def test_actual_response_model_or_tier_cannot_use_a_different_rate_card(collected):
    root,_,_=collected
    path=root/'native.stderr'
    path.write_text(path.read_text().replace('gpt-6.1-sol','different-model'))
    with pytest.raises(ValueError,match='controls'):
        trials.collect(root,0)
