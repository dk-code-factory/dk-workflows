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

import pytest

ROOT=Path(__file__).resolve().parents[1]
PR_PACKAGE=ROOT
FIXTURE=ROOT/'tests/fixtures/recipe'
PR='https://github.com/owner/repo/pull/7'
REPO='github.com/owner/repo'


@pytest.mark.parametrize('url', ['ssh://git@github.com/owner/repo.git',
                               'git://github.com/owner/repo.git',
                               'http://github.com/owner/repo.git'])
def test_offline_git_transport_rejects_other_network_schemes(tmp_path, url):
    remote = tmp_path/'remote.json'
    remote.write_text('{}')
    wrapper = tmp_path/'git'
    wrapper.write_text((FIXTURE/'remote_cli.py').read_text())
    executed = tmp_path/'executed'
    real_git = tmp_path/'real-git'
    real_git.write_text('#!'+sys.executable+'\nfrom pathlib import Path\nPath('+repr(str(executed))+').touch()\n')
    real_git.chmod(0o755)
    env = {**os.environ, 'DK_RECIPE_REMOTE': str(remote),
           'DK_RECIPE_FIXTURE': str(ROOT/'tests/pr/fixtures/github_remote.py'),
           'DK_RECIPE_BARE': str(tmp_path/'bare'), 'DK_RECIPE_GIT': str(real_git)}
    result = subprocess.run([sys.executable, str(wrapper), 'ls-remote', '--refs', url],
                            env=env, text=True, capture_output=True, timeout=10)
    assert result.returncode == 1
    assert 'external Git access' in result.stderr
    assert not executed.exists()


@pytest.mark.parametrize('final_ci_outcome',['success','rerun','repair'])
def test_complete_recipe_resume_cleanup_and_unchanged_reruns(tmp_path,final_ci_outcome):
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
    # Issue #103: unrelated product-documentation conflicts must not become a
    # global preparation gate for this value-change PR. Keep the contradictory
    # source evidence in the actual pinned trees throughout the whole recipe.
    unrelated_docs={
        'DESIGN.md':'Startup invalidates old live execution.\n',
        'RECOVERY.md':'Startup automatically resumes eligible old live execution.\n',
        'PROVIDERS.md':'Provider adapters only read remote state.\n',
        'PUBLICATION.md':'Provider adapters may submit reviews under server policy.\n',
    }
    for name,text in unrelated_docs.items():(checkout/name).write_text(text)
    git('add','.');git('commit','-qm','baseline');base=git('rev-parse','HEAD')
    git('checkout','-qb','pr-branch')
    (checkout/'code.py').write_text('VALUE = 0\n')
    git('commit','-qam','initial change');head=git('rev-parse','HEAD')
    bare=tmp_path/'remote.git';execute([real_git,'init','--bare','-q',bare])
    execute([real_git,'-C',checkout,'push',str(bare),'HEAD:refs/heads/pr-branch'])
    fixture_spec=importlib.util.spec_from_file_location('recipe_fixture',PR_PACKAGE/'tests/pr/fixtures/github_remote.py')
    fixture=importlib.util.module_from_spec(fixture_spec);fixture_spec.loader.exec_module(fixture)
    state=fixture.initial();state['metadata']['headRefOid']=head;state['run']['head_sha']=head
    state['checks'][0].update(bucket='pending',state='PENDING')
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
    ci_heads=[]
    def ci(current,rerun=None,*,ok=True):
        ci_heads.append(current)
        args=['--pr',PR,'--head',current,'--no-checks-grace','0','--timeout','1']
        if rerun:args+=['--rerun-operation',rerun]
        return command(PR_PACKAGE/'runtime/dk-ci/scripts/ci_wait.py',*args,ok=ok)
    installed=tmp_path/'selected-skills';installed.mkdir()
    for name in ('domain','a','b'):(installed/(name+'.md')).write_text('Native fixture skill '+name)
    context=save('context.json',{'scope':'Return two.','decisions':['Return two.'],'instructions':'Fix this PR.'})
    applicable_rules=save('applicable-rules.json',{'rules':['The settled value must be an integer.']})
    def spec(skill,task='content-review',current=None,cacheable=True):
        definition={'skill_identity':'fixture:'+skill,'resources':{'skill':str(installed/(skill+'.md'))},
                    'responsibility':task,
                    'assigned_instructions':'Perform '+task+' against the complete pinned scope and instructions.',
                    'instructions':{},'inputs':{'context':str(context),'rules':str(applicable_rules)},
                    'options':{},'cacheable':cacheable,'governing_inputs_complete':True,
                    'git':{'checkout':str(checkout),'target':base,'base':base,'head':current or git('rev-parse','HEAD')}}
        if skill in ('a','b'):
            definition['resources']['implementation']=str(FIXTURE/'reviewer.py')
        value=cache('spec','--definition',save('definition.json',definition))
        return save(skill+'-'+task+'.json',value)
    def record(task,claim,value,progress=None):
        output=save('output-'+claim['id']+'.json',value)
        args=['--spec',task,'--claim',claim['id'],'--original',output,'--normalized',output,
              '--status','complete' if value['complete'] else 'incomplete']
        if progress:args+=['--progress',save('progress.json',progress)]
        return cache('record',*args)
    def domain(task,perform,*,complete=True,**budget):
        task_spec=spec('domain',task,cacheable=False)
        args=['--spec',task_spec,'--run','iteration']
        for key,value in budget.items():
            args+=['--'+key.replace('_','-')]
            if value is not True:args+=[value]
        claim=cache('start',*args)['claim'];result=perform()
        record(task_spec,claim,{'complete':complete,'result':result},progress={'stage':task,'complete':complete})
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
    # Pending checks do not gate review/fix cycles or commit cleanup.
    reviewer_log=tmp_path/'reviewer-launches.jsonl'
    def start_reviews(skills,cycle,*,recover=None,retry=False,run='iteration'):
        tasks={skill:spec(skill) for skill in skills}
        args=['--run',run,'--cycle',cycle]
        for task in tasks.values():args+=['--spec',task]
        for key,claim in (recover or {}).items():args+=['--recover',key+'='+claim]
        if retry:args+=['--retry']
        batch=cache('start-batch',*args)
        assert len(batch['tasks'])==len(tasks)
        return {skill:(task,started) for (skill,task),started in zip(tasks.items(),batch['tasks'])}
    def review(skill,cycle,*,claimed=None,retry=False,fail=False,run='iteration'):
        task,started=claimed or start_reviews((skill,),cycle,retry=retry,run=run)[skill]
        if started['action']=='reuse':return started
        assert started['action']=='invoke'
        output=tmp_path/('review-'+started['claim']['id']+'.json')
        argv=[sys.executable,FIXTURE/'reviewer.py','--skill',skill,'--checkout',checkout,'--log',reviewer_log,'--output',output]
        if fail:argv+=['--fail']
        execute(argv,env=env)
        value=json.loads(output.read_text());record(task,started['claim'],value)
        return value
    initial=start_reviews(('a','b'),'c1')
    assert review('a','c1',claimed=initial['a'])['findings']
    interrupted=initial['b'][1]
    # Successful a is recorded immediately while sibling b remains unfinished.
    resumed=start_reviews(('a','b'),'c1')
    assert resumed['b'][1]['action']=='recover_required'
    assert review('a','c1',claimed=resumed['a'])['action']=='reuse'
    recovered=start_reviews(('b',),'c1',recover={interrupted['key']:interrupted['claim']['id']})
    assert review('b','c1',claimed=recovered['b'],fail=True)['complete'] is False
    assert review('b','c1',retry=True)['findings']
    def fix():
        old=git('rev-parse','HEAD');(checkout/'code.py').write_text('VALUE = 2\n')
        execute([sys.executable,'-c','exec(open("code.py").read()); assert VALUE == 2'],env=env,cwd=checkout)
        assert (checkout/'code.py').read_text()=='VALUE = 2\n'
        git('commit','-qam','fix the reviewed value');new=git('rev-parse','HEAD')
        push(old,new);return {'head':new,'disposition':{'value':'fixed'}}
    head=domain('fix-findings',fix)['head']
    domain('partition-after-fix',lambda:{'outcome':'retain','reason':'Reviewed fix remains within scope'})
    refreshed=start_reviews(('a','b'),'c2')
    assert not review('a','c2',claimed=refreshed['a'])['findings']
    assert not review('b','c2',claimed=refreshed['b'])['findings']
    reviewed_tree=git('rev-parse','HEAD^{tree}')
    def cleanup():
        old=git('rev-parse','HEAD');git('reset','--soft',base);git('commit','-qm','Coherent value change')
        candidate=git('rev-parse','HEAD');assert git('rev-parse','HEAD^{tree}')==reviewed_tree
        push(old,candidate)
        return {'outcome':'published','reviewed_head':old,'head':candidate,'tree':reviewed_tree,
                'reasons':['One coherent behavior change with documentation'],'target':base}
    head=domain('cleanup',cleanup)['head']
    # Squash changes only provenance: final content review coverage is retained.
    for skill in ('a','b'):assert review(skill,'c3')['action']=='reuse'
    reply=domain('reply',lambda:publish({'kind':'reply-inline','pr':PR,'head':head,'parent':41,'body':'Verified answer'}))
    domain('resolve',lambda:publish({'kind':'resolve-thread','pr':PR,'head':head,'thread_id':'PRRT_fixture','reply_id':reply['receipt']['id']}))
    assert not ci_heads and not remote_value().get('check_reads')
    assert remote_value()['checks'][0]['bucket']=='pending'
    final_head=head
    rerun=None
    if final_ci_outcome=='rerun':
        # The first observation is at the final gate. A verified rerun opens
        # the next attempt window without allocating another review cycle.
        update_remote(lambda v:v.update(checks=[]))
        unresolved=domain('ci',lambda:ci(head,ok=False),complete=False)
        assert unresolved['result']=='no_checks'
        rerun=domain('rerun',lambda:publish({'kind':'ci-rerun','pr':PR,'head':head,'run_id':77,'attempt':1,
                                          'reason':'Verified transient fixture outage'}),ci_rerun=True,head=head)
    elif final_ci_outcome=='repair':
        # An unknown final failure remains blocking. Its code repair changes
        # review inputs, so refresh coverage and cleanup before observing again.
        update_remote(lambda v:v.update(checks=[{**fixture.initial()['checks'][0],
                                               'bucket':'fail','state':'FAILURE'}]))
        failed=domain('ci',lambda:ci(head,ok=False),complete=False)
        assert failed['result']=='failure' and failed['blockers']
        assert cache('status','--run','iteration','--spec',spec('domain','ci',cacheable=False))['tasks'][0]['completion']=='incomplete'
        def repair():
            old=git('rev-parse','HEAD')
            (checkout/'code.py').write_text('VALUE = 2\nCI_VALIDATED = True\n')
            execute([sys.executable,'-c','exec(open("code.py").read()); assert VALUE == 2 and CI_VALIDATED'],env=env,cwd=checkout)
            git('commit','-qam','repair final CI');new=git('rev-parse','HEAD')
            push(old,new);return {'head':new,'validated':True}
        head=domain('ci-repair',repair,ci_repair=True,cycle='c2')['head']
        domain('partition-after-ci-repair',lambda:{'outcome':'retain','reason':'CI repair remains within scope'})
        changed=cache('status','--spec',spec('a'),'--spec',spec('b'))
        assert all(not task['reusable'] for task in changed['tasks'])
        repaired=start_reviews(('a','b'),'c3')
        assert not review('a','c3',claimed=repaired['a'])['findings']
        assert not review('b','c3',claimed=repaired['b'])['findings']
        reviewed_tree=git('rev-parse','HEAD^{tree}')
        head=domain('cleanup',cleanup)['head']
        for skill in ('a','b'):assert review(skill,'c3')['action']=='reuse'
        assert ci_heads==[final_head] and head!=final_head
        assert set(remote_value()['check_reads'])=={final_head}
    update_remote(lambda v:v.update(checks=fixture.initial()['checks']))
    final_ci=domain('ci',lambda:ci(head,rerun['operation'] if rerun else None))
    assert final_ci['result']=='success' and final_ci['head']==head
    assert ci_heads==([head] if final_ci_outcome=='success' else [final_head,head])
    assert set(remote_value()['check_reads'])==set(ci_heads)
    domain('re-review',lambda:publish({'kind':'request-review','pr':PR,'head':head,'reviewers':['reviewer']}))
    domain('complete',lambda:{'head':head,'coverage':['a','b'],'findings':[],'ci':'success','cleanup':'published'})
    status=cache('status','--compact','--run','iteration','--spec',spec('a'),'--spec',spec('b'))
    assert all(t['reusable'] and not t['findings'] for t in status['tasks'])
    run=status['runs']['iteration']
    assert run['cycles']==(['c1','c2','c3'] if final_ci_outcome=='repair' else ['c1','c2'])
    assert list(run['retries'].values())==[1]
    assert run['ci_reruns']==({head:1} if rerun else {}) and not status['unfinished']
    assert run['ci_repairs']==({'c2':1} if final_ci_outcome=='repair' else {})
    assert run['cycle']==run['cycles'][-1] and run['progress_count']>=6 and 'progress' not in run
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
    for name,text in unrelated_docs.items():
        assert (checkout/name).read_text()==text
        assert git('show',head+':'+name)==text.strip()
