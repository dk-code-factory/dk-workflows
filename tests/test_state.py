"""Behavioral acceptance tests; every reviewer invocation is counted by its caller."""
from copy import deepcopy
from concurrent.futures import ThreadPoolExecutor
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

import pytest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'runtime'))
import state
from state import Store, capture, read

REPO='github.com/acme/project'
PR='https://github.com/acme/project/pull/7'
ROOT=Path(__file__).resolve().parents[1]


def git(root,*args):
    return subprocess.check_output(['git','-C',str(root),*args],text=True).strip()


@pytest.fixture
def case(tmp_path, monkeypatch):
    # The process probe measures an eligible checkout, independently of the
    # caller's includes, Git directories and execution configuration.
    for name in list(os.environ):
        if name.startswith('GIT_'):
            monkeypatch.delenv(name)
    monkeypatch.setenv('GIT_CONFIG_NOSYSTEM', '1')
    monkeypatch.setenv('GIT_CONFIG_GLOBAL', os.devnull)
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
    assert [task['reusable'] for task in store.status([content,history,head_check])['tasks']] == [True,False,False]
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


def require_git_context_paths(checkout):
    variables = git(checkout, 'var', '-l').splitlines()
    for name in ('GIT_ATTR_SYSTEM', 'GIT_ATTR_GLOBAL', 'GIT_CONFIG_GLOBAL'):
        if not any(line.startswith(name+'=') for line in variables):
            pytest.skip('This Git does not expose the paths needed for safe capture sharing')


@pytest.mark.parametrize('linked', [False, True])
def test_status_batch_process_count_comparison(case, tmp_path, monkeypatch, linked):
    """Reproduce the issue's two completed-reviewer recipe, without model calls."""
    a, store = case
    require_git_context_paths(a['git']['checkout'])
    if linked:
        checkout = tmp_path/'linked'
        git(a['git']['checkout'], 'worktree', 'add', '--detach', '-q', str(checkout), a['git']['head'])
        a['git']['checkout'] = str(checkout)
    b = deepcopy(a); b['skill']['identity'] = 'reviewer:b'
    done(store, a); done(store, b)
    retained = read(store.manifest)
    original_git = state.git
    calls = []
    def counted(*args):
        calls.append(args)
        return original_git(*args)
    monkeypatch.setattr(state, 'git', counted)
    def probe():
        calls.clear()
        assert all(t['reusable'] for t in store.status([a, b])['tasks'])
        assert store.start(a)['action'] == store.start(b)['action'] == 'reuse'
        assert all(t['reusable'] for t in store.status([a, b])['tasks'])
        assert read(store.manifest) == retained
        return len(calls)
    # Independent captures reproduce the pre-optimization implementation.
    with monkeypatch.context() as legacy:
        legacy.setattr(state._CaptureContext, 'git', lambda self, source: state._capture_git(source))
        before = probe()
    after = probe()
    assert before == 42 and after == 28
    print(f'capture-related Git processes: independent={before}, status-batched={after}')


def test_status_batch_mixed_modes_process_count_comparison(case, monkeypatch):
    a, store = case
    checkout = Path(a['git']['checkout'])
    require_git_context_paths(checkout)
    (checkout/'code').write_text('third distinct tree\n')
    git(checkout, 'commit', '-qam', 'third commit')
    a['git'].update(base=a['git']['head'], head=git(checkout, 'rev-parse', 'HEAD'))
    specs = []
    for mode in ('content', 'history', 'head_sensitive'):
        spec = deepcopy(a); spec['task']['id'] = mode
        if mode != 'content': spec['git'][mode] = True
        specs.append(spec); done(store, spec)
    retained = read(store.manifest)
    calls = []
    original = state.git
    def counted(*args):
        calls.append(args)
        return original(*args)
    monkeypatch.setattr(state, 'git', counted)
    def probe():
        calls.clear()
        assert all(task['reusable'] for task in store.status(specs)['tasks'])
        assert all(store.start(spec)['action'] == 'reuse' for spec in specs)
        assert all(task['reusable'] for task in store.status(specs, compact=True)['tasks'])
        assert read(store.manifest) == retained
        return len(calls)
    with monkeypatch.context() as legacy:
        legacy.setattr(state._CaptureContext, 'git', lambda self, source: state._capture_git(source))
        before = probe()
    after = probe()
    assert before == 66 and after == 54
    print(f'mixed-mode capture-related Git processes: independent={before}, status-batched={after}')


@pytest.mark.parametrize('kind', ['config', 'attributes'])
def test_status_captures_git_reported_system_paths(case, tmp_path, monkeypatch, kind):
    """Emulate an installation whose system paths use another prefix."""
    a, store = case
    require_git_context_paths(a['git']['checkout'])
    system_config = tmp_path/'prefix/etc/gitconfig'
    system_config.parent.mkdir(parents=True)
    system_config.write_text('[diff]\n\tnoprefix = false\n')
    attributes = system_config.with_name('gitattributes')
    attributes.write_text('code -diff\n')
    monkeypatch.delenv('GIT_CONFIG_NOSYSTEM')
    original = state.git
    global_attributes = original(a['git']['checkout'], 'var', 'GIT_ATTR_GLOBAL').strip()
    def installation(checkout, *args):
        # Real Git consumes these files; its path receipt represents the
        # alternate prefix without requiring a second compiled Git binary.
        prefix = ['-c', 'core.attributesfile='+str(attributes)] if kind == 'attributes' else []
        result = subprocess.check_output(['git', '-C', str(checkout), *prefix, *args],
                                         env={**os.environ, 'GIT_CONFIG_SYSTEM': str(system_config)},
                                         stderr=subprocess.PIPE)
        if args == ('var', '-l') and kind == 'attributes':
            result = b'\n'.join(
                b'GIT_ATTR_SYSTEM='+os.fsencode(attributes) if line.startswith(b'GIT_ATTR_SYSTEM=')
                else b'GIT_ATTR_GLOBAL='+global_attributes if line.startswith(b'GIT_ATTR_GLOBAL=')
                else line for line in result.split(b'\n'))
        return result
    monkeypatch.setattr(state, 'git', installation)
    b = deepcopy(a); b['skill']['identity'] = 'reviewer:b'
    done(store, a); done(store, b)
    def specs():
        yield a
        if kind == 'config': system_config.write_text('[diff]\n\tnoprefix = true\n')
        else: attributes.write_text('code diff\n')
        yield b
    tasks = store.status(specs())['tasks']
    assert tasks[0]['reusable'] and not tasks[1]['reusable']


def test_relative_xdg_config_home_declines_capture_sharing(case, monkeypatch):
    spec, store = case
    monkeypatch.setenv('XDG_CONFIG_HOME', 'relative-config')
    assert state._git_context(spec['git']) is None
    assert not store.status([spec])['tasks'][0]['reusable']


@pytest.mark.parametrize('history,head_sensitive', [(False, False), (True, False), (False, True), (True, True)])
def test_batch_capture_matches_independent_fingerprints_and_provenance(case, monkeypatch, history, head_sensitive):
    a, store = case
    a['git'].update(history=history, head_sensitive=head_sensitive)
    b = deepcopy(a)
    b['skill']['identity'] = 'reviewer:b'
    b['task'] = {'id': 'other', 'instructions': 'Different assigned instructions'}
    b['options'] = {'strict': True}
    b['cacheable'] = False
    expected = [capture(a), capture(b)]
    observed = []
    original = state.capture
    def collect(spec, **kwargs):
        value = original(spec, **kwargs)
        observed.append(value)
        return value
    monkeypatch.setattr(state, 'capture', collect)
    store.status([a, b])
    assert observed == expected
    observed[0]['components']['inputs']['git']['trees']['head'] = 'altered'
    assert observed[1]['components']['inputs']['git']['trees']['head'] != 'altered'


@pytest.mark.parametrize('field', ['target', 'base', 'head', 'history', 'head_sensitive', 'checkout'])
def test_status_batch_separates_different_git_inputs(case, tmp_path, monkeypatch, field):
    a, store = case
    b = deepcopy(a); b['skill']['identity'] = 'reviewer:b'
    if field in {'target', 'base', 'head'}:
        b['git'][field] = a['git']['base'] if field == 'head' else a['git']['head']
    elif field == 'checkout':
        other = tmp_path/'other'
        git(tmp_path, 'clone', '-q', a['git']['checkout'], str(other))
        b['git']['checkout'] = str(other)
    else:
        b['git'][field] = True
    expected = [capture(a), capture(b)]
    observed = []
    original = state.capture
    def collect(spec, **kwargs):
        value = original(spec, **kwargs)
        observed.append(value)
        return value
    monkeypatch.setattr(state, 'capture', collect)
    store.status([a, b])
    assert observed == expected


@pytest.mark.parametrize('ref', ['HEAD', 'refs/heads/moving'])
def test_status_resolves_mutable_refs_freshly_inside_one_batch(case, ref):
    a, store = case
    checkout = a['git']['checkout']
    if ref == 'refs/heads/moving':
        git(checkout, 'update-ref', ref, a['git']['head'])
    a['git']['head'] = ref
    b = deepcopy(a); b['skill']['identity'] = 'reviewer:b'
    done(store, a); done(store, b)
    def specs():
        yield a
        git(checkout, 'update-ref', ref, a['git']['base'])
        yield b
    tasks = store.status(specs())['tasks']
    assert tasks[0]['reusable']
    assert not tasks[1]['reusable']


@pytest.mark.parametrize('revision', ['abbreviated', 'expression'])
def test_status_does_not_share_unpinned_git_calculations(case, monkeypatch, revision):
    spec, store = case
    head = spec['git']['head']
    spec['git']['head'] = head[:12] if revision == 'abbreviated' else head+'^0'
    calls = []
    original = state.git
    def counted(*args):
        calls.append(args)
        return original(*args)
    monkeypatch.setattr(state, 'git', counted)
    store.status([spec, deepcopy(spec)])
    assert len(calls) == 14


@pytest.mark.parametrize('packed', [False, True])
def test_status_does_not_share_hex_ref_names(case, monkeypatch, packed):
    spec, store = case
    checkout = spec['git']['checkout']
    git(checkout, 'update-ref', 'refs/tags/'+spec['git']['head'], spec['git']['base'])
    if packed:
        git(checkout, 'pack-refs', '--all')
    calls = []
    original = state.git
    def counted(*args):
        calls.append(args)
        return original(*args)
    monkeypatch.setattr(state, 'git', counted)
    store.status([spec, deepcopy(spec)])
    assert len(calls) == 14


@pytest.mark.parametrize('kind', ['resource', 'context', 'instruction', 'assigned', 'option', 'missing', 'noncacheable-missing', 'invalid-option'])
def test_status_batch_keeps_each_tasks_mutable_checks(case, kind):
    a, store = case
    b = deepcopy(a); b['skill']['identity'] = 'reviewer:b'
    if kind == 'noncacheable-missing':
        b['cacheable'] = False
    done(store, a); done(store, b)
    def specs():
        yield a
        if kind == 'resource': Path(b['skill']['resources']['entry']).write_text('new reviewer')
        elif kind == 'context': Path(b['inputs']['context']).write_text('new context')
        elif kind == 'instruction': Path(b['instructions']['user']).write_text('new instruction')
        elif kind == 'assigned': b['task']['instructions'] = 'new task'
        elif kind == 'option': b['options']['strict'] = True
        elif kind in {'missing', 'noncacheable-missing'}: Path(b['skill']['resources']['entry']).unlink()
        else: b['git']['history'] = 1
        yield b
    if kind in {'missing', 'noncacheable-missing', 'invalid-option'}:
        with pytest.raises((OSError, ValueError)):
            store.status(specs())
    else:
        tasks = store.status(specs())['tasks']
        assert tasks[0]['reusable'] and not tasks[1]['reusable']


@pytest.mark.parametrize('kind', ['config', 'attributes', 'index-attributes', 'environment', 'replace'])
def test_status_batch_refreshes_changed_execution_context(case, monkeypatch, kind):
    a, store = case
    checkout = Path(a['git']['checkout'])
    if kind in {'attributes', 'index-attributes'}:
        (checkout/'.gitattributes').write_text('code -diff\n')
        if kind == 'index-attributes':
            git(checkout, 'add', '.gitattributes')
            (checkout/'.gitattributes').unlink()
    b = deepcopy(a); b['skill']['identity'] = 'reviewer:b'
    done(store, a); done(store, b)
    calculations = []
    original = state._capture_git
    def counted(*args, **kwargs):
        calculations.append(args)
        return original(*args, **kwargs)
    monkeypatch.setattr(state, '_capture_git', counted)
    def specs():
        yield a
        if kind == 'config': git(checkout, 'config', 'diff.noprefix', 'true')
        elif kind == 'attributes': (checkout/'.gitattributes').write_text('code diff\n')
        elif kind == 'index-attributes': git(checkout, 'rm', '--cached', '-q', '.gitattributes')
        elif kind == 'environment': monkeypatch.setenv('GIT_CONFIG_COUNT', '1'); monkeypatch.setenv('GIT_CONFIG_KEY_0', 'diff.noprefix'); monkeypatch.setenv('GIT_CONFIG_VALUE_0', 'true')
        else: git(checkout, 'replace', a['git']['head'], a['git']['base'])
        yield b
    tasks = store.status(specs())['tasks']
    assert tasks[0]['reusable']
    if kind != 'index-attributes':
        assert not tasks[1]['reusable']
    # The Git version may not consult indexed attributes for this tree diff,
    # but changing the index must still prevent sharing its earlier capture.
    assert len(calculations) == 2


@pytest.mark.parametrize('kind', ['attribute-tree', 'bom-include', 'worktree', 'signature'])
def test_status_falls_back_for_indirect_git_inputs(case, tmp_path, kind):
    a, store = case
    checkout = Path(a['git']['checkout'])
    if kind == 'attribute-tree':
        attributes = checkout/'.gitattributes'
        attributes.write_text('code -diff\n')
        git(checkout, 'add', '.gitattributes'); git(checkout, 'commit', '-qm', 'binary attributes')
        first = git(checkout, 'rev-parse', 'HEAD')
        attributes.write_text('code diff\n')
        git(checkout, 'commit', '-qam', 'text attributes')
        second = git(checkout, 'rev-parse', 'HEAD')
        git(checkout, 'update-ref', 'refs/heads/attributes', first)
        git(checkout, 'config', 'attr.tree', 'refs/heads/attributes')
        # Older Git releases do not use attr.tree. They must still bypass
        # sharing because the same configuration is indirect on newer Git.
        def change():
            git(checkout, 'update-ref', 'refs/heads/attributes', second)
        field = 'diff_sha256'
    elif kind == 'bom-include':
        included = tmp_path/'included.gitconfig'
        included.write_text('[diff]\n\tnoprefix = false\n')
        config = checkout/'.git/config'
        config.write_bytes(b'\xef\xbb\xbf[include]\n\tpath = '+os.fsencode(included)+b'\n'+config.read_bytes())
        def change():
            included.write_text('[diff]\n\tnoprefix = true\n')
        field = 'diff_sha256'
    elif kind == 'worktree':
        redirected = tmp_path/'redirected'; redirected.mkdir()
        (redirected/'code').write_text('review me\n')
        attributes = redirected/'.gitattributes'
        attributes.write_text('code -diff\n')
        git(checkout, 'config', 'core.worktree', str(redirected))
        def change():
            attributes.write_text('code diff\n')
        field = 'diff_sha256'
    else:
        if not shutil.which('ssh-keygen'):
            pytest.skip('SSH signature regression requires ssh-keygen')
        key = tmp_path/'signing-key'
        subprocess.check_call(['ssh-keygen', '-q', '-t', 'ed25519', '-N', '', '-f', str(key)])
        signers = tmp_path/'allowed-signers'
        signers.write_text('test@example.test '+key.with_suffix('.pub').read_text())
        for name, value in [('gpg.format', 'ssh'), ('user.signingkey', str(key)),
                            ('gpg.ssh.allowedSignersFile', str(signers)), ('log.showSignature', 'true')]:
            git(checkout, 'config', name, value)
        (checkout/'code').write_text('signed change\n')
        git(checkout, 'commit', '-S', '-qam', 'signed change')
        a['git'].update(head=git(checkout, 'rev-parse', 'HEAD'), history=True)
        def change():
            signers.write_text('')
        field = 'history'
    b = deepcopy(a); b['skill']['identity'] = 'reviewer:b'
    before = capture(a)['components']['inputs']['git'][field]
    done(store, a); done(store, b)
    # The optimization deliberately declines these inputs even when this Git
    # version uses the original working directory for tree-diff attributes.
    assert state._git_context(a['git']) is None
    expected = []
    def specs():
        yield a
        change()
        expected.append(capture(b)['components']['inputs']['git'][field])
        yield b
    tasks = store.status(specs())['tasks']
    assert tasks[0]['reusable']
    assert tasks[1]['reusable'] is (before == expected[0])
    if kind in {'bom-include', 'signature'}:
        assert expected[0] != before


@pytest.mark.parametrize('metadata', ['gitfile', 'commondir'])
def test_status_preserves_whitespace_in_git_metadata_paths(case, tmp_path, monkeypatch, metadata):
    a, store = case
    checkout = Path(a['git']['checkout'])
    linked = tmp_path/'linked'
    git(checkout, 'worktree', 'add', '-q', '--detach', str(linked), a['git']['head'])
    gitfile = linked/'.git'
    directory = Path(os.fsdecode(gitfile.read_bytes()).removeprefix('gitdir: ').rstrip('\r\n'))
    if metadata == 'commondir':
        common = tmp_path/'common '
        shutil.copytree(checkout/'.git', common)
        (directory/'commondir').write_bytes(os.fsencode(common)+b'\n')
    else:
        moved = tmp_path/'admin '
        directory.rename(moved)
        (moved/'commondir').write_bytes(os.fsencode(checkout/'.git')+b'\n')
        gitfile.write_bytes(b'gitdir: '+os.fsencode(moved)+b'\n')
        # A trimmed sibling must not be mistaken for the actual Git directory.
        (tmp_path/'admin').mkdir()
    a['git']['checkout'] = str(linked)
    b = deepcopy(a); b['skill']['identity'] = 'reviewer:b'
    before = capture(a)
    done(store, a); done(store, b)
    expected = []
    def specs():
        yield a
        git(linked, 'config', 'diff.noprefix', 'true')
        expected.append(capture(b))
        yield b
    observed = []
    original = state.capture
    def collect(spec, **kwargs):
        value = original(spec, **kwargs)
        observed.append(value)
        return value
    monkeypatch.setattr(state, 'capture', collect)
    tasks = store.status(specs())['tasks']
    assert expected[0]['fingerprint'] != before['fingerprint']
    assert observed == [before, expected[0]]
    assert tasks[0]['reusable'] and not tasks[1]['reusable']


def test_git_drift_after_status_still_rejects_completion(case):
    spec, store = case
    store.status([spec, deepcopy(spec)])
    claim = store.start(spec)['claim']
    spec['git']['head'] = spec['git']['base']
    with pytest.raises(ValueError, match='changed during'):
        store.record(spec, claim['id'], b'x', {'complete': True}, 'complete')
    assert store.status([spec])['tasks'][0]['unfinished']['id'] == claim['id']


@pytest.mark.parametrize('condition', ['complete', 'failed', 'incomplete', 'damaged', 'missing',
                                       'invalidated', 'changed', 'unfinished', 'noncacheable'])
def test_compact_and_full_task_decisions_match(case,condition):
    spec,store=case
    if condition=='noncacheable':spec['cacheable']=False
    claim=store.start(spec,run='run',cycle='c1')['claim']
    if condition!='unfinished':
        status=condition if condition in {'failed','incomplete'} else 'complete'
        store.record(spec,claim['id'],b'original',
                     {'complete':status=='complete','findings':[{'id':'f1'}]},status,
                     progress={'dispositions':{'f1':'open'}})
        if condition in {'damaged','missing'}:
            path=Path(store.status([spec])['tasks'][0]['result'])
            if condition=='damaged':path.write_text('{}')
            else:path.unlink()
        elif condition=='invalidated':store.invalidate([claim['key']],'Review again')
        elif condition=='changed':Path(spec['inputs']['context']).write_text('Changed scope')
    before=store.manifest.read_bytes()
    full=store.status([spec]);compact=store.status([spec],compact=True)
    assert compact['tasks']==full['tasks'] and compact['unfinished']==full['unfinished']
    assert compact['tasks'][0]['reusable']==(condition=='complete')
    assert {k:v for k,v in compact.items() if k!='runs'}=={k:v for k,v in full.items() if k!='runs'}
    assert {k:v for k,v in compact['runs']['run'].items()
            if k not in {'cycle','progress_count','progress_ref'}}=={
                k:v for k,v in full['runs']['run'].items() if k!='progress'}
    assert store.manifest.read_bytes()==before


def test_selected_compact_run_keeps_other_runs_unfinished_claims_and_cli_full_access(case):
    spec,store=case
    done(store,spec,run='selected',cycle='c1')
    other=deepcopy(spec);other['task']['id']='other-task'
    claim=store.start(other,run='other',cycle='c1')['claim']
    specpath=store.root/'spec.json';specpath.write_text(json.dumps(spec))
    command=[sys.executable,str(ROOT/'scripts/dk.py'),'--repository',REPO,'--pr',PR,
             'status','--spec',str(specpath)]
    compact=json.loads(subprocess.check_output(command+['--compact','--run','selected'],text=True))
    full=json.loads(subprocess.check_output(command+['--run','selected'],text=True))
    assert list(compact['runs'])==list(full['runs'])==['selected']
    assert compact['unfinished']==full['unfinished']==[claim]
    assert full['runs']['selected']['progress']==[]
    assert compact['runs']['selected']['cycle']=='c1'
    assert store.start(other,run='other',cycle='c1')['action']=='recover_required'
    assert set(store.status([],compact=True)['runs'])=={'selected','other'}
    missing=subprocess.run(command+['--compact','--run','unknown'],capture_output=True,text=True)
    assert missing.returncode==2 and 'Unknown run id' in json.loads(missing.stdout)['reason']
    with pytest.raises(ValueError,match='Unknown run id'):store.status([],run='unknown')


def test_compact_progress_is_bounded_and_all_original_evidence_remains_retrievable(case):
    import base64
    spec,store=case;spec['cacheable']=False
    run='run/with~escapes'
    originals=[]
    first_size=None
    for i in range(30):
        claim=store.start(spec,run=run)['claim']
        original=b'Original output\x00'+bytes([i])
        progress={'dispositions':{'f1':'accepted'},
                  'acceptances':[{'instruction':'Accept f1 '+('evidence '*1000),
                                  'source':f'user message {i}'}]}
        recorded=store.record(spec,claim['id'],original,{'complete':True,'findings':[{'id':'f1'}]},
                              'complete',progress=progress)
        originals.append((recorded['result'],original,progress))
        if i==0:first_size=len(json.dumps(store.status([],compact=True)))
    compact=store.status([],compact=True)
    summary=compact['runs'][run]
    assert summary['cycle'] is None and summary['progress_count']==30 and 'progress' not in summary
    assert len(json.dumps(compact))<=first_size+2
    reference=summary['progress_ref']
    value=read(reference['manifest'])
    for token in reference['pointer'].split('/')[1:]:
        value=value[token.replace('~1','/').replace('~0','~')]
    assert value==store.status([],run=run)['runs'][run]['progress']
    assert [p['value'] for p in value]==[p for _,_,p in originals]
    for path,original,_ in originals:
        envelope=read(path)
        assert base64.b64decode(envelope['original'],validate=True)==original
        assert envelope['normalized']['findings']==[{'id':'f1'}]
    attempts=read(store.manifest)['entries'][capture(spec)['key']]['attempts']
    assert [a['result'] for a in attempts]==[Path(path).name for path,_,_ in originals]


def test_compact_inspection_and_recovery_preserve_exhausted_budgets(case):
    spec,store=case;spec['cacheable']=False
    for i in range(5):done(store,spec,run='run',cycle=f'c{i}')
    for _ in range(3):done(store,spec,run='run',ci_rerun=True,head='head')
    for _ in range(5):done(store,spec,run='run',ci_repair=True)
    first=store.start(spec,run='run',cycle='c4',retry=True)['claim']
    before=store.manifest.read_bytes()
    retained=Store(REPO,PR).status([spec],compact=True,run='run')
    assert retained['unfinished']==[first] and retained['runs']['run']['cycle']=='c4'
    assert store.manifest.read_bytes()==before
    resumed=store.start(spec,recover=first['id'],run='run',cycle='c4')
    store.record(spec,resumed['claim']['id'],b'recovered',{'complete':True},'complete',
                 progress={'recovery':'Stopped prior agent and reconciled domain writes'})
    for kwargs,reason in [({'cycle':'c5'},'Five'),({'cycle':'c4','retry':True},'retry'),
                          ({'ci_rerun':True,'head':'head'},'reruns'),({'ci_repair':True},'attempts')]:
        with pytest.raises(ValueError,match=reason):store.start(spec,run='run',**kwargs)
    run=store.status([],compact=True,run='run')['runs']['run']
    assert run['cycles']==[f'c{i}' for i in range(5)]
    assert run['ci_reruns']=={'head':3} and run['ci_repairs']=={'0':5}
    assert list(run['retries'].values())==[1] and run['progress_count']==1
    assert store.status([],run='run')['runs']['run']['progress'][0]['value']['recovery']
