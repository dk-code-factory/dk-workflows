"""Behavioral acceptance tests; every reviewer invocation is counted by its caller."""
from copy import deepcopy
from concurrent.futures import ThreadPoolExecutor
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'runtime'))
from state import Store, capture, read

REPO='github.com/acme/project'
PR='https://github.com/acme/project/pull/7'
ROOT=Path(__file__).resolve().parents[1]


def git(root,*args):
    return subprocess.check_output(['git','-C',str(root),*args],text=True).strip()


@pytest.fixture
def case(tmp_path):
    checkout=tmp_path/'repo';checkout.mkdir()
    git(checkout,'init','-q')
    git(checkout,'config','user.name','Test')
    git(checkout,'config','user.email','test@example.test')
    (checkout/'code').write_text('base\n')
    git(checkout,'add','.');git(checkout,'commit','-qm','baseline')
    base=git(checkout,'rev-parse','HEAD')
    (checkout/'code').write_text('review me\n')
    git(checkout,'commit','-qam','change')
    head=git(checkout,'rev-parse','HEAD')
    skill=tmp_path/'reviewer.md';skill.write_text('Review everything')
    context=tmp_path/'context';context.write_text('scope and decisions')
    instruction=tmp_path/'instructions';instruction.write_text('original user instruction')
    spec={'repository':REPO,'pr':PR,'skill':{'identity':'reviewer:a','resources':{'entry':str(skill)}},
          'task':{'id':'content-review','instructions':'Review complete pinned code against supplied context.'},
          'inputs':{'context':str(context)},'instructions':{'user':str(instruction)},
          'options':{},'governing_inputs_complete':True,
          'git':{'checkout':str(checkout),'base':base,'target':base,'head':head}}
    return spec,Store(REPO,PR,checkout=checkout)


def done(store,spec,**kwargs):
    started=store.start(spec,**kwargs)
    assert started['action']=='invoke'
    store.record(spec,started['claim']['id'],b'Original reviewer output\x00',{'complete':True,'findings':[]},'complete')
    return started


def test_unchanged_reruns_across_sessions_and_iteration_launch_zero_reviewers(case):
    spec,store=case
    done(store,spec)
    specpath=store.root/'spec.json';specpath.write_text(json.dumps(spec))
    command=[sys.executable,str(ROOT/'scripts/dk.py'),'--repository',REPO,'--pr',PR,'start','--spec',str(specpath),
             '--run','new-iteration','--cycle','pass-1']
    for _ in range(2):
        response=json.loads(subprocess.check_output(command,text=True))
        assert response['action']=='reuse'
    assert store.status([spec])['runs']=={}


def test_complete_findings_are_reused_without_claiming_clean(case):
    spec,store=case
    claim=store.start(spec)['claim']
    original=b'One actionable finding'
    store.record(spec,claim['id'],original,{'complete':True,'findings':[{'id':'f1','text':'bug'}]},'complete')
    status=store.start(spec)
    assert status['action']=='reuse' and status['findings'][0]['id']=='f1'
    assert read(status['result'])['original_sha256']
    assert 'ready' not in status


def test_reviewer_resource_change_and_added_reviewer_preserve_siblings(case,tmp_path):
    a,store=case
    b=deepcopy(a);b['skill']={'identity':'reviewer:b','resources':{'entry':str(tmp_path/'b.md')}}
    Path(b['skill']['resources']['entry']).write_text('B')
    done(store,a);done(store,b)
    Path(a['skill']['resources']['entry']).write_text('A changed')
    assert not store.status([a])['tasks'][0]['reusable']
    assert store.start(b)['action']=='reuse'
    c=deepcopy(a);c['skill']['identity']='reviewer:c'
    assert store.start(c)['action']=='invoke'
    assert store.start(b)['action']=='reuse'


@pytest.mark.parametrize('input_kind',['context','user','assigned','option','code','target','base'])
def test_changed_governing_inputs_require_fresh_execution(case,input_kind):
    spec,store=case;done(store,spec)
    if input_kind=='context':Path(spec['inputs']['context']).write_text('scope changed')
    elif input_kind=='user':Path(spec['instructions']['user']).write_text('instruction changed')
    elif input_kind=='assigned':spec['task']['instructions']='Use new governing task'
    elif input_kind=='option':spec['options']={'strict':True}
    else:
        checkout=Path(spec['git']['checkout'])
        (checkout/'code').write_text('changed tree\n');git(checkout,'commit','-qam','tree change')
        spec['git'][{'code':'head'}.get(input_kind,input_kind)]=git(checkout,'rev-parse','HEAD')
    assert store.start(spec)['action']=='invoke'


def test_only_consumers_of_changed_scope_are_invalidated(case):
    spec,store=case
    other=deepcopy(spec);other['task']['id']='code-only';other['inputs']={}
    done(store,spec);done(store,other)
    Path(spec['inputs']['context']).write_text('New Decisions')
    assert not store.status([spec])['tasks'][0]['reusable']
    assert store.start(other)['action']=='reuse'


def test_squash_keeps_content_reviews_but_refreshes_history_and_head_checks(case):
    content,store=case
    history=deepcopy(content);history['task']['id']='history-review';history['git']['history']=True
    head_check=deepcopy(content);head_check['task']['id']='head-check';head_check['git']['head_sensitive']=True
    for spec in (content,history,head_check):done(store,spec)
    checkout=content['git']['checkout']
    git(checkout,'commit','--amend','-qm','regrouped message')
    new=git(checkout,'rev-parse','HEAD')
    for spec in (content,history,head_check):spec['git']['head']=new
    assert store.start(content)['action']=='reuse'
    assert store.start(history)['action']=='invoke'
    assert store.start(head_check)['action']=='invoke'


@pytest.mark.parametrize('status',['failed','incomplete'])
def test_partial_failure_preserves_completed_siblings(case,status):
    a,store=case;b=deepcopy(a);b['skill']['identity']='reviewer:b'
    done(store,a,run='run',cycle='c1')
    claim=store.start(b,run='run',cycle='c1')['claim']
    store.record(b,claim['id'],b'Usage limit',{'complete':False,'findings':[]},status)
    assert store.start(a,run='run',cycle='c1')['action']=='reuse'
    assert store.start(b,run='run',cycle='c1',retry=True)['action']=='invoke'
    assert store.status([])['runs']['run']['cycles']==['c1']


def test_interrupted_claim_requires_explicit_recovery_and_retains_budget(case):
    spec,store=case
    first=store.start(spec,run='run',cycle='c1')
    assert store.start(spec,run='run',cycle='c1')['action']=='recover_required'
    with pytest.raises(ValueError,match='retain'):
        store.start(spec,recover=first['claim']['id'],run='new-run',cycle='c1')
    resumed=store.start(spec,recover=first['claim']['id'],run='run',cycle='c1')
    assert resumed['action']=='invoke'
    status=store.status([])
    assert status['runs']['run']['cycles']==['c1']
    assert len(status['unfinished'])==1
    assert read(store.manifest)['entries'][capture(spec)['key']]['attempts'][0]['status']=='interrupted'


def test_concurrent_start_launches_only_one_task(case):
    spec,store=case
    with ThreadPoolExecutor(max_workers=6) as pool:
        starts=list(pool.map(lambda _:store.start(spec),range(6)))
    assert sum(s['action']=='invoke' for s in starts)==1
    assert sum(s['action']=='recover_required' for s in starts)==5


def test_five_cycles_retry_and_acceptances_survive_resume(case):
    spec,store=case
    for i in range(5):
        task=deepcopy(spec);task['task']['id']=f'pass-{i}'
        done(store,task,run='run',cycle=f'c{i}')
    fresh=deepcopy(spec);fresh['task']['id']='sixth'
    with pytest.raises(ValueError,match='Five'):
        store.start(fresh,run='run',cycle='c5')
    # Retrying an existing cycle does not open another cycle or lose recorded human evidence.
    original=store.start(spec,run='run',cycle='c4')['claim']
    store.record(spec,original['id'],b'usage limit',{'complete':False},'incomplete')
    claim=store.start(spec,run='run',cycle='c4',retry=True)['claim']
    store.record(spec,claim['id'],b'limit',{'complete':False},'incomplete',
                 progress={'acceptances':[{'instruction':'Accept f1','source':'user message 3'}]})
    with pytest.raises(ValueError,match='retry'):
        store.start(spec,run='run',cycle='c4',retry=True)
    retained=Store(REPO,PR).status([])['runs']['run']
    assert len(retained['cycles'])==5
    assert retained['progress'][0]['value']['acceptances'][0]['source']=='user message 3'
    assert not store.status([spec])['tasks'][0]['reusable']


@pytest.mark.parametrize('flag,limit',[('ci_repair',5),('ci_rerun',3)])
def test_ci_budgets_are_reserved_before_invocation_and_retained(case,flag,limit):
    spec,store=case;spec['cacheable']=False
    for i in range(limit):
        started=store.start(spec,run='run',head=spec['git']['head'],**{flag:True})
        store.record(spec,started['claim']['id'],b'result',{'complete':True},'complete')
    with pytest.raises(ValueError,match='attempts|reruns'):
        store.start(spec,run='run',head=spec['git']['head'],**{flag:True})


def test_damaged_or_invalidated_results_are_never_reused(case):
    spec,store=case;done(store,spec)
    result=store.start(spec)['result'];Path(result).write_text('{}')
    assert not store.status([spec])['tasks'][0]['reusable']
    second=store.start(spec)
    store.record(spec,second['claim']['id'],b'rebuilt',{'complete':True},'complete')
    store.invalidate([capture(spec)['key']],'Require fresh evidence')
    assert store.start(spec)['action']=='invoke'


def test_record_rejects_input_drift_and_wrong_claim(case):
    spec,store=case;claim=store.start(spec)['claim']
    with pytest.raises(ValueError,match='another'):
        store.record(spec,'wrong',b'x',{'complete':True},'complete')
    Path(spec['inputs']['context']).write_text('changed while running')
    with pytest.raises(ValueError,match='changed during'):
        store.record(spec,claim['id'],b'x',{'complete':True},'complete')
    assert store.status([spec])['tasks'][0]['unfinished']['id']==claim['id']


def test_uncapturable_inputs_execute_without_cache_and_missing_resources_fail(case):
    spec,store=case;spec['governing_inputs_complete']=False
    with pytest.raises(ValueError,match='complete governing'):
        store.start(spec)
    spec['cacheable']=False
    done(store,spec)
    assert not store.status([spec])['tasks'][0]['reusable']
    spec['skill']['resources']['missing']='/missing/reviewer/resource'
    with pytest.raises(OSError):store.start(spec)


def test_state_cannot_live_inside_a_checkout(case,monkeypatch):
    spec,store=case
    monkeypatch.setenv('XDG_STATE_HOME',spec['git']['checkout']+'/state')
    with pytest.raises(ValueError,match='outside'):
        Store(REPO,PR,checkout=spec['git']['checkout'])


def test_public_completion_cannot_claim_failed_or_incomplete_success(case):
    spec,store=case;claim=store.start(spec)['claim']
    for normalized,status in [({'complete':False},'complete'),({'complete':True},'failed')]:
        with pytest.raises(ValueError,match='completion'):
            store.record(spec,claim['id'],b'x',normalized,status)


def test_changed_inputs_cannot_be_relaunched_under_an_existing_cycle(case):
    spec,store=case;done(store,spec,run='run',cycle='c1')
    Path(spec['inputs']['context']).write_text('Changed scope')
    with pytest.raises(ValueError,match='new cycle'):
        store.start(spec,run='run',cycle='c1')
    assert store.status([])['runs']['run']['cycles']==['c1']
    assert store.start(spec,run='run',cycle='c2')['action']=='invoke'


def test_changed_inputs_during_interruption_recover_into_a_new_cycle(case):
    spec,store=case;first=store.start(spec,run='run',cycle='c1')
    Path(spec['inputs']['context']).write_text('New governing instructions')
    with pytest.raises(ValueError,match='new cycle'):
        store.start(spec,recover=first['claim']['id'],run='run',cycle='c1')
    resumed=store.start(spec,recover=first['claim']['id'],run='run',cycle='c2')
    assert resumed['action']=='invoke'
    assert store.status([])['runs']['run']['cycles']==['c1','c2']


def test_missing_resources_cannot_trap_an_interrupted_claim_or_reset_budgets(case):
    spec,store=case;first=store.start(spec,run='run',cycle='c1')
    Path(spec['skill']['resources']['entry']).unlink()
    store.invalidate([first['claim']['key']],'Stopped old agent; reconciled its domain writes',recover=first['claim']['id'])
    retained=store.status([])
    assert retained['unfinished']==[] and retained['runs']['run']['cycles']==['c1']
    entry=read(store.manifest)['entries'][first['claim']['key']]
    assert entry['attempts'][0]['status']=='interrupted' and entry['invalidated']
