import json
import pytest
from update_fixtures import Fixture


@pytest.mark.parametrize('host',['codex','claude'])
@pytest.mark.parametrize('mode',['notify','auto','off'])
def test_nested_plugins_defer_until_outer_completes(tmp_path,host,mode):
    f=Fixture(tmp_path,'nested',mode,host)
    outer=f.invoke();inner=f.invoke('code-review-qodo',parent=outer['token'])
    assert f.invoke('code-review-qodo','complete',token=inner['token'])['status']=='deferred'
    assert not [a for a in f.calls if a[1:3] in (['plugin','add'],['plugin','update'])]
    done=f.invoke(phase='complete',token=outer['token'])
    assert len(done['updates'])==(2 if mode=='auto' else 0)
    assert all(u['reload_required'] for u in done['updates'])
    assert len(f.network)==(0 if mode=='off' else 2)


def test_parallel_outer_invocations_also_defer(tmp_path):
    f=Fixture(tmp_path,'parallel','auto')
    one=f.invoke();two=f.invoke();inner=f.invoke('code-review-qodo',parent=two['token'])
    assert f.invoke(phase='complete',token=one['token'])['status']=='deferred'
    assert f.invoke(phase='complete',token=two['token'])['status']=='deferred'
    done=f.invoke('code-review-qodo','complete',token=inner['token'])
    assert len(done['updates'])==2
    assert not f.invoke(phase='complete',token=one['token'])['updates']


def test_completion_failure_preserves_earlier_success_and_state(tmp_path):
    f=Fixture(tmp_path,'failure','auto');outer=f.invoke();inner=f.invoke('code-review-qodo',parent=outer['token'])
    f.invoke('code-review-qodo','complete',token=inner['token'])
    f.records.append(dict(f.records[1],scope='project'))
    done=f.invoke(phase='complete',token=outer['token'])
    assert [u['status'] for u in done['updates']]==['updated','update_failed']
    assert done['updates'][0]['reload_required'] is True
    assert f.state()['invocations'][outer['token']]['complete'] is True


def test_cache_deduplicates_notices_and_checks(tmp_path):
    f=Fixture(tmp_path,'cache');one=f.invoke();f.invoke(phase='complete',token=one['token']);two=f.invoke()
    assert one['action']=='notify' and two['action']=='continue'
    assert len(f.network)==1
    state=f.state();state['checks']['dk-code-factory/dk-workflows']['checked']-=86401
    (f.base/'state/dk-code-factory/updates/state.json').write_text(json.dumps(state))
    f.invoke();assert len(f.network)==2


def test_offline_operation_is_nonblocking_and_cached(tmp_path):
    f=Fixture(tmp_path,'offline')
    def offline(*a,**kw):f.network.append('offline');raise OSError('offline')
    f.urlopen=offline
    one=f.invoke();two=f.invoke()
    assert one['status']==two['status']=='offline' and one['token']
    assert len(f.network)==1
    assert f.invoke(phase='complete',token=one['token'])['status']=='deferred'


def test_pinned_version_does_not_update(tmp_path):
    f=Fixture(tmp_path,'pin','auto');f.records[0]['source']['ref']='v1.0.0'
    one=f.invoke();assert one['status']=='pinned'
    assert not f.invoke(phase='complete',token=one['token'])['updates']


def test_policy_change_cancels_pending_update(tmp_path):
    f=Fixture(tmp_path,'policy','auto');one=f.invoke()
    (f.base/'config/dk-code-factory/updates.json').write_text('{"mode":"off"}')
    assert not f.invoke(phase='complete',token=one['token'])['updates']


def test_plugin_override_does_not_install_other_plugin(tmp_path):
    f=Fixture(tmp_path,'override','auto')
    (f.base/'config/dk-code-factory/updates.json').write_text('{"mode":"auto","plugins":{"code-review-qodo":"off"}}')
    one=f.invoke();two=f.invoke('code-review-qodo',parent=one['token'])
    f.invoke('code-review-qodo','complete',token=two['token'])
    assert [u['plugin'] for u in f.invoke(phase='complete',token=one['token'])['updates']]==['dk-workflows']


def test_uncertain_host_update_cannot_claim_success(tmp_path):
    f=Fixture(tmp_path,'uncertain','auto');one=f.invoke();original=f.subrun
    def unchanged(argv,**kwargs):
        if argv[1:3]==['plugin','add']:
            import subprocess
            return subprocess.CompletedProcess(argv,0,'','')
        return original(argv,**kwargs)
    f.subrun=unchanged
    done=f.invoke(phase='complete',token=one['token'])
    assert done['updates'][0]['status']=='update_failed'


def test_already_newer_installation_is_not_reinstalled(tmp_path):
    f=Fixture(tmp_path,'newer','auto');one=f.invoke();f.records[0]['version']='2.0.0'
    done=f.invoke(phase='complete',token=one['token'])
    assert done['updates'][0]['status']=='current'
    assert not [a for a in f.calls if a[1:3]==['plugin','add']]


def test_local_entry_in_remote_marketplace_can_auto_update(tmp_path):
    f=Fixture(tmp_path,'remote','auto')
    f.records[0]['source']={'source':'local','path':'./'}
    f.records[0]['marketplaceSource']={'sourceType':'git','source':'https://github.com/dk-code-factory/dk-workflows.git','ref':'main'}
    one=f.invoke();assert one['status']=='deferred'
    assert f.invoke(phase='complete',token=one['token'])['updates'][0]['status']=='updated'
