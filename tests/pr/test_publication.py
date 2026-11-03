"""A lost response cannot cause a second remote mutation on resume."""
from concurrent.futures import ThreadPoolExecutor
import importlib.util
from pathlib import Path
import subprocess

import pytest
from shared import publication
from shared.diagnostics import Diagnostic

ROOT=Path(__file__).resolve().parents[2]
spec=importlib.util.spec_from_file_location('domain_publish',ROOT/'scripts/publish.py')
publisher=importlib.util.module_from_spec(spec);spec.loader.exec_module(publisher)


@pytest.mark.parametrize('kind',['reply-inline','push','edit-pr'])
def test_lost_response_reconciles_once_and_never_sends_twice(tmp_path,kind):
    remote={'applied':False};sends=[]
    prepared=publication.prepare(kind,'remote-target',{'head':'old'},{'body':'reviewed'},['domain-publisher'])
    operation=prepared['operation']
    def send(value):
        sends.append(value['operation_id']);remote['applied']=True
        raise subprocess.TimeoutExpired(['send'],1)
    def observe(value):
        if not remote['applied']:raise ValueError('No confirmed receipt')
        return {'head':'new','url':'remote-receipt'}
    with pytest.raises(Diagnostic,match='reconciliation'):
        publication.execute(operation,kind,check=lambda *_:None,send=send,observe=observe)
    assert publication.load(operation)['status']=='pending'
    for _ in range(2):
        assert publication.execute(operation,kind,check=lambda *_:None,send=send,observe=observe,reconcile=True)['status']=='verified'
    assert len(sends)==1


def test_uncertain_absent_write_and_changed_readiness_never_authorize_resend():
    prepared=publication.prepare('reply','target',{}, {}, ['publisher'])
    sent=[]
    def send(_):sent.append(1);raise OSError('connection lost')
    def absent(_):raise ValueError('No unique matching remote reply')
    for _ in range(2):
        with pytest.raises(Diagnostic):
            publication.execute(prepared['operation'],'reply',check=lambda *_:None,send=send,observe=absent)
    assert sent==[1]
    def blocked(*_):raise ValueError('Scope changed')
    with pytest.raises(Diagnostic,match='current readiness'):
        publication.execute(prepared['operation'],'reply',check=blocked,send=send,observe=lambda _: {'id':7})
    assert publication.load(prepared['operation'])['status']=='verified'
    assert sent==[1]


def test_concurrent_publication_uses_one_operation_lock():
    prepared=publication.prepare('edit-pr','target',{}, {}, ['publisher'])
    sent=[]
    def send(_):sent.append(1);return {'response':'ok'}
    with ThreadPoolExecutor(max_workers=4) as pool:
        results=list(pool.map(lambda _:publication.execute(prepared['operation'],'edit-pr',
            check=lambda *_:None,send=send,observe=lambda _: {'verified':True}),range(4)))
    assert len(sent)==1 and all(r['status']=='verified' for r in results)


def test_description_helper_preserves_and_reconciles_exact_payload(monkeypatch):
    pr='https://github.com/owner/repo/pull/7'
    state={'url':pr,'headRefOid':'a'*40,'title':'old','body':'old body'}
    monkeypatch.setattr(publisher,'metadata',lambda _:dict(state))
    calls=[]
    def run(argv,**_):
        calls.append(argv)
        assert argv[:3]==['gh','pr','edit']
        state['body']=Path(argv[argv.index('--body-file')+1]).read_text()
        raise OSError('Lost response after applying description')
    monkeypatch.setattr(publisher,'run',run)
    request={'kind':'edit-pr','pr':pr,'head':'a'*40,'fields':{'body':'new body\n## Decisions\n- Keep exact choice'}}
    prepared=publisher.prepare(request)
    with pytest.raises(Diagnostic):publisher.publish(prepared['operation'])
    assert publisher.publish(prepared['operation'],reconcile=True)['status']=='verified'
    assert len(calls)==1 and state['title']=='old'


def test_repeated_prepare_recovers_operation_even_after_remote_change(monkeypatch):
    pr='https://github.com/owner/repo/pull/7'
    state={'url':pr,'headRefOid':'a'*40,'title':'old','body':'old body'}
    monkeypatch.setattr(publisher,'metadata',lambda _:dict(state))
    request={'kind':'edit-pr','pr':pr,'head':'a'*40,'fields':{'body':'new body'}}
    first=publisher.prepare(request)
    # The write may have applied even if the caller lost every response.
    state.update(body='new body',headRefOid='b'*40)
    recovered=publisher.prepare(request)
    assert recovered['operation']==first['operation']
    assert recovered['reused_intent'] is True


def test_inline_reply_helper_reconciles_lost_write_without_a_second_reply(monkeypatch):
    pr='https://github.com/owner/repo/pull/7'
    monkeypatch.setattr(publisher,'metadata',lambda _: {'url':pr,'headRefOid':'a'*40,'title':'title','body':'body'})
    remote=[];sends=[]
    monkeypatch.setattr(publisher,'replies',lambda _:list(remote))
    def api(pr,path,*,payload=None):
        if path=='user':return {'login':'author'}
        sends.append(path)
        assert path.endswith('/pulls/7/comments/123/replies')
        remote.append({'id':99,'body':payload['body'],'user':{'login':'author'},'html_url':'https://reply/99'})
        raise OSError('Reply applied but response lost')
    monkeypatch.setattr(publisher,'api',api)
    request={'kind':'reply-inline','pr':pr,'head':'a'*40,'parent':123,'body':'Verified fix'}
    operation=publisher.prepare(request)['operation']
    with pytest.raises(Diagnostic):publisher.publish(operation)
    assert publisher.prepare(request)['operation']==operation
    assert publisher.publish(operation,reconcile=True)['receipt']['id']==99
    assert publisher.publish(operation)['status']=='verified' and len(sends)==1


def test_push_helper_reconciles_lost_exact_lease_push(monkeypatch,tmp_path):
    remote=['a'*40];sends=[]
    monkeypatch.setattr(publisher,'remote_head',lambda _:remote[0])
    def run(argv,**kwargs):
        if argv[:2]==['git','check-ref-format']:return ''
        if argv[:2]==['git','rev-parse']:return 'c'*40
        assert argv[:2]==['git','push'] and '--force-with-lease=refs/heads/pr-branch:'+'a'*40 in argv
        sends.append(argv);remote[0]='b'*40
        raise OSError('Push applied but response lost')
    monkeypatch.setattr(publisher,'run',run)
    request={'kind':'push','checkout':str(tmp_path),'remote':'https://github.com/owner/repo.git',
             'branch':'pr-branch','head':'b'*40,'expected_head':'a'*40}
    operation=publisher.prepare(request)['operation']
    with pytest.raises(Diagnostic):publisher.publish(operation)
    assert publisher.prepare(request)['operation']==operation
    assert publisher.publish(operation,reconcile=True)['receipt']['head']=='b'*40
    assert publisher.publish(operation)['status']=='verified' and len(sends)==1


def test_new_branch_uses_an_exact_absence_lease(monkeypatch,tmp_path):
    remote=[None];sends=[]
    monkeypatch.setattr(publisher,'remote_head',lambda _:remote[0])
    def run(argv,**kwargs):
        if argv[:2]==['git','check-ref-format']:return ''
        if argv[:2]==['git','rev-parse']:return 'c'*40
        assert argv[:2]==['git','push'] and '--force-with-lease=refs/heads/new-branch:' in argv
        sends.append(argv);remote[0]='b'*40;return ''
    monkeypatch.setattr(publisher,'run',run)
    operation=publisher.prepare({'kind':'push','create_branch':True,'expected_head':None,'checkout':str(tmp_path),
                                'remote':'https://github.com/owner/repo.git','branch':'new-branch','head':'b'*40})['operation']
    assert publisher.publish(operation)['status']=='verified'
    assert publisher.publish(operation,reconcile=True)['status']=='verified' and len(sends)==1


def test_moved_publisher_recovers_original_intent_with_current_diagnostic_command(monkeypatch):
    pr='https://github.com/owner/repo/pull/7'
    state={'url':pr,'headRefOid':'a'*40,'title':'old','body':'old'}
    monkeypatch.setattr(publisher,'metadata',lambda _:dict(state))
    request={'kind':'edit-pr','pr':pr,'head':'a'*40,'fields':{'body':'new'}}
    operation=publisher.prepare(request)['operation']
    path=Path(operation)
    saved=publication.load(path)
    saved['reconcile_argv']=['python3','/retired/plugin/scripts/publish.py','reconcile','--operation',operation]
    from shared.private_state import write
    write(path,saved)
    sends=[]
    def run(argv,**kwargs):
        sends.append(argv);state['body']='new';raise OSError('Response lost after publication')
    monkeypatch.setattr(publisher,'run',run)
    with pytest.raises(Diagnostic) as failure:publisher.publish(operation)
    argv=failure.value.action['argv']
    assert str(ROOT/'scripts/publish.py') in argv
    assert '/retired/plugin/scripts/publish.py' not in argv
    assert publisher.prepare(request)['operation']==operation
    assert publisher.publish(operation,reconcile=True)['status']=='verified'
    assert len(sends)==1
