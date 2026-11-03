"""Drive the agent's public boundaries across processes, real Git and offline gh.

This fixture coordinator is test code. Production workflows have no launcher;
selected native skill invocations are explicit here as they are in the recipe.
"""
import importlib.util
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

ROOT=Path(__file__).resolve().parents[1]
PR_PACKAGE=ROOT
FIXTURE=ROOT/'tests/fixtures/recipe'
PR='https://github.com/owner/repo/pull/7'
REPO='github.com/owner/repo'


def test_complete_recipe_resume_cleanup_and_unchanged_reruns(tmp_path):
    def execute(argv, *, env=None, ok=True, cwd=None):
        result=subprocess.run([str(v) for v in argv],env=env,text=True,capture_output=True,cwd=cwd)
        if ok:assert result.returncode==0,result.stdout+result.stderr
        return result
    def git(*args):return execute([real_git,'-C',checkout,*args]).stdout.strip()
    def save(name,value):
        path=tmp_path/name;path.write_text(json.dumps(value));return path
    assert (PR_PACKAGE/'scripts/publish.py').is_file(), 'Bundled PR publisher must be installed with the workflow'
    real_git=shutil.which('git')
    checkout=tmp_path/'repo';checkout.mkdir()
    git('init','-q');git('config','user.name','Fixture');git('config','user.email','fixture@example.test')
    (checkout/'code.py').write_text('VALUE = 1\n')
    git('add','.');git('commit','-qm','baseline');base=git('rev-parse','HEAD')
    git('checkout','-qb','pr-branch')
    (checkout/'code.py').write_text('VALUE = 0\n')
    git('commit','-qam','initial change');head=git('rev-parse','HEAD')
    bare=tmp_path/'remote.git';execute([real_git,'init','--bare','-q',bare])
    execute([real_git,'-C',checkout,'push',str(bare),'HEAD:refs/heads/pr-branch'])
    fixture_spec=importlib.util.spec_from_file_location('recipe_fixture',PR_PACKAGE/'tests/pr/fixtures/github_remote.py')
    fixture=importlib.util.module_from_spec(fixture_spec);fixture_spec.loader.exec_module(fixture)
    state=fixture.initial();state['metadata']['headRefOid']=head;state['run']['head_sha']=head
    state['branches'].update(main=base,**{'pr-branch':head})
    state['lose_response']=['push','edit-pr','reply-inline','resolve-thread','request-review','ci-rerun']
    remote=save('remote.json',state)
    bin_path=tmp_path/'bin';bin_path.mkdir()
    for executable in ('gh','git'):
        path=bin_path/executable;path.write_text('#!'+sys.executable+'\n'+(FIXTURE/'remote_cli.py').read_text());path.chmod(0o755)
    env={**os.environ,'PATH':str(bin_path)+os.pathsep+os.environ['PATH'],'DK_RECIPE_REMOTE':str(remote),
         'DK_RECIPE_FIXTURE':str(PR_PACKAGE/'tests/pr/fixtures/github_remote.py'),
         'DK_RECIPE_BARE':str(bare),'DK_RECIPE_GIT':real_git}
    def remote_value():return json.loads(remote.read_text())
    def update_remote(fn):
        value=remote_value();fn(value);remote.write_text(json.dumps(value))
    def command(script,*args,ok=True):
        result=execute([sys.executable,script,*args],env=env,ok=ok)
        return json.loads(result.stdout) if result.stdout.strip() else None
    def cache(action,*args,ok=True):
        return command(ROOT/'scripts/dk.py','--repository',REPO,'--pr',PR,'--checkout',checkout,action,*args,ok=ok)
    def publish(request):
        request_file=save('request.json',request)
        prepared=command(PR_PACKAGE/'scripts/publish.py','prepare','--file',request_file)
        result=command(PR_PACKAGE/'scripts/publish.py','publish','--operation',prepared['operation'],ok=False)
        if result.get('status')!='verified':
            # A new process resumes the same prepared intent. Never prepare a replacement.
            result=command(PR_PACKAGE/'scripts/publish.py','reconcile','--operation',prepared['operation'])
        assert result['status']=='verified'
        return result
    def push(old,new):
        return publish({'kind':'push','checkout':str(checkout),'remote':'https://github.com/owner/repo.git',
                        'branch':'pr-branch','head':new,'expected_head':old})
    def ci(current,rerun=None):
        args=['--pr',PR,'--head',current,'--no-checks-grace','0','--timeout','1']
        if rerun:args+=['--rerun-operation',rerun]
        return command(PR_PACKAGE/'runtime/dk-ci/scripts/ci_wait.py',*args)
    installed=tmp_path/'selected-skills';installed.mkdir()
    for name in ('domain','a','b'):(installed/(name+'.md')).write_text('Native fixture skill '+name)
    context=save('context.json',{'scope':'Return two.','decisions':['Return two.'],'instructions':'Fix this PR.'})
    def spec(skill,task='content-review',current=None,cacheable=True):
        value={'repository':REPO,'pr':PR,'skill':{'identity':'fixture:'+skill,'resources':{'skill':str(installed/(skill+'.md'))}},
               'task':{'id':task,'instructions':'Perform '+task+' against the complete pinned scope and instructions.'},
               'inputs':{'context':str(context)},'options':{},'cacheable':cacheable,'governing_inputs_complete':True,
               'git':{'checkout':str(checkout),'target':base,'base':base,'head':current or git('rev-parse','HEAD')}}
        if skill in ('a','b'):
            value['skill']['resources']['implementation']=str(FIXTURE/'reviewer.py')
        return save(skill+'-'+task+'.json',value)
    def record(task,claim,value,progress=None):
        output=save('output-'+claim['id']+'.json',value)
        args=['--spec',task,'--claim',claim['id'],'--original',output,'--normalized',output,
              '--status','complete' if value['complete'] else 'incomplete']
        if progress:args+=['--progress',save('progress.json',progress)]
        return cache('record',*args)
    def domain(task,perform,**budget):
        task_spec=spec('domain',task,cacheable=False)
        args=['--spec',task_spec,'--run','iteration']
        for key,value in budget.items():
            args+=['--'+key.replace('_','-')]
            if value is not True:args+=[value]
        claim=cache('start',*args)['claim'];result=perform()
        record(task_spec,claim,{'complete':True,'result':result},progress={'stage':task,'complete':True})
        return result
    # Inspect instructions/Decisions and complete feedback before changing code.
    domain('inspect',lambda:{'scope':'Return two.','feedback':[{'source':PR+'#discussion_r41','text':'Document the value'}]})
    def feedback():
        (checkout/'README.md').write_text('This change returns the settled value.\n')
        git('add','.');git('commit','-qm','document the change');new=git('rev-parse','HEAD')
        push(head,new);return {'head':new,'validated':True}
    head=domain('feedback',feedback)['head']
    body='Scope: return two.\n## Decisions\n- Return two.\nThe behavior is specified above.'
    domain('description',lambda:publish({'kind':'edit-pr','pr':PR,'head':head,'fields':{'body':body}}))
    domain('decisions',lambda:{'decisions':['Return two.'],'source':PR})
    domain('partition',lambda:{'outcome':'retain','added_lines_threshold':1000,'reason':'Below the configured gate'})
    # Existing CI observation is unresolved; a targeted rerun opens its next window.
    update_remote(lambda v:v.update(checks=[]))
    unresolved=command(PR_PACKAGE/'runtime/dk-ci/scripts/ci_wait.py','--pr',PR,'--head',head,'--no-checks-grace','0',ok=False)
    assert unresolved['result']=='no_checks'
    rerun=domain('rerun',lambda:publish({'kind':'ci-rerun','pr':PR,'head':head,'run_id':77,'attempt':1,
                                      'reason':'Verified transient fixture outage'}),ci_rerun=True,head=head)
    update_remote(lambda v:v.update(checks=fixture.initial()['checks']))
    assert domain('ci',lambda:ci(head,rerun['operation']))['result']=='success'
    reviewer_log=tmp_path/'reviewer-launches.jsonl'
    def review(skill,cycle,*,recover=None,retry=False,fail=False,run='iteration'):
        task=spec(skill)
        args=['--spec',task,'--run',run,'--cycle',cycle]
        if recover:args+=['--recover',recover]
        if retry:args+=['--retry']
        started=cache('start',*args)
        if started['action']=='reuse':return started
        assert started['action']=='invoke'
        output=tmp_path/('review-'+started['claim']['id']+'.json')
        argv=[sys.executable,FIXTURE/'reviewer.py','--skill',skill,'--checkout',checkout,'--log',reviewer_log,'--output',output]
        if fail:argv+=['--fail']
        execute(argv,env=env)
        value=json.loads(output.read_text());record(task,started['claim'],value)
        return value
    assert review('a','c1')['findings']
    interrupted=cache('start','--spec',spec('b'),'--run','iteration','--cycle','c1')
    assert cache('start','--spec',spec('b'),'--run','iteration','--cycle','c1')['action']=='recover_required'
    assert review('a','c1')['action']=='reuse'
    assert review('b','c1',recover=interrupted['claim']['id'],fail=True)['complete'] is False
    assert review('b','c1',retry=True)['findings']
    def fix():
        old=git('rev-parse','HEAD');(checkout/'code.py').write_text('VALUE = 2\n')
        execute([sys.executable,'-c','exec(open("code.py").read()); assert VALUE == 2'],env=env,cwd=checkout)
        assert (checkout/'code.py').read_text()=='VALUE = 2\n'
        git('commit','-qam','fix the reviewed value');new=git('rev-parse','HEAD')
        push(old,new);return {'head':new,'disposition':{'value':'fixed'}}
    head=domain('fix-findings',fix)['head']
    assert domain('ci-after-fix',lambda:ci(head))['result']=='success'
    assert not review('a','c2')['findings'] and not review('b','c2')['findings']
    reviewed_tree=git('rev-parse','HEAD^{tree}')
    def cleanup():
        old=git('rev-parse','HEAD');git('reset','--soft',base);git('commit','-qm','Coherent value change')
        candidate=git('rev-parse','HEAD');assert git('rev-parse','HEAD^{tree}')==reviewed_tree
        push(old,candidate)
        return {'outcome':'published','reviewed_head':old,'head':candidate,'tree':reviewed_tree,
                'reasons':['One coherent behavior change with documentation'],'target':base}
    head=domain('cleanup',cleanup)['head']
    assert domain('ci-after-cleanup',lambda:ci(head))['result']=='success'
    # Squash changes only provenance: final content review coverage is retained.
    for skill in ('a','b'):assert review(skill,'c3')['action']=='reuse'
    reply=domain('reply',lambda:publish({'kind':'reply-inline','pr':PR,'head':head,'parent':41,'body':'Verified answer'}))
    domain('resolve',lambda:publish({'kind':'resolve-thread','pr':PR,'head':head,'thread_id':'PRRT_fixture','reply_id':reply['receipt']['id']}))
    domain('re-review',lambda:publish({'kind':'request-review','pr':PR,'head':head,'reviewers':['reviewer']}))
    domain('complete',lambda:{'head':head,'coverage':['a','b'],'findings':[],'ci':'success','cleanup':'published'})
    status=cache('status','--compact','--run','iteration','--spec',spec('a'),'--spec',spec('b'))
    assert all(t['reusable'] and not t['findings'] for t in status['tasks'])
    run=status['runs']['iteration']
    assert run['cycles']==['c1','c2'] and list(run['retries'].values())==[1]
    assert run['ci_reruns']=={rerun['receipt']['head']:1} and not status['unfinished']
    assert run['cycle']=='c2' and run['progress_count']>=6 and 'progress' not in run
    detailed=cache('status','--run','iteration')['runs']['iteration']
    assert {p['value']['stage'] for p in detailed['progress']} >= {'inspect','feedback','partition','ci','cleanup','complete'}
    before=reviewer_log.read_text()
    for run_id in ('standalone-review','subsequent-iteration'):
        for skill in ('a','b'):assert review(skill,'first',run=run_id)['action']=='reuse'
    assert reviewer_log.read_text()==before
    # Fresh publication checks on a subsequent iteration do not duplicate writes.
    before_writes=remote_value()['writes'].copy()
    publish({'kind':'edit-pr','pr':PR,'head':head,'fields':{'body':body}})
    publish({'kind':'reply-inline','pr':PR,'head':head,'parent':41,'body':'Verified answer'})
    publish({'kind':'request-review','pr':PR,'head':head,'reviewers':['reviewer']})
    assert remote_value()['writes']==before_writes
    assert git('ls-remote',str(bare),'refs/heads/pr-branch').split()[0]==head
