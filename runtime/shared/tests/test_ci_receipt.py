"""Durable receipts share the original observer lock and retained deadline."""
import os
from pathlib import Path
import pytest
from shared.ci_observation import Observation, PermissionDenied
from shared.private_state import read

PR='https://github.com/owner/repo/pull/7'
HEAD='a'*40


def result(status='pending'):
    return {'head':HEAD,'result':status,'checks':[{'name':'test','bucket':'pending' if status=='pending' else 'pass',
            'link':'attempt-1','workflow':'Validate','startedAt':'2026-11-05T12:00:00Z'}]}


def test_receipt_pending_resume_and_final_evidence_keep_original_deadline(tmp_path):
    path=tmp_path/'observation.json'
    with Observation(path,PR,HEAD,timeout=30,extension=0,clock=lambda:10,receipt=True) as observer:
        budget=observer.update(result())
        packet=read(path,{})['current']['receipt']
        assert packet['deadline']==40 and packet['state']=='pending'
        assert packet['identity']['observation_id']==budget['observation_id']
        assert packet['identity']['head']==HEAD
        assert budget['observer_receipt']['pointer']=='/current/receipt'
    with Observation(path,PR,HEAD,timeout=900,clock=lambda:20) as observer:
        assert observer.current['receipt']['state']=='observing'
        assert observer.current['receipt']['final_evidence'] is None
        assert observer.current['receipt']['deadline']==40
        observer.update(result('success'))
        final={**result('success'),'raw_result':'success','blockers':[],'verified_at':'now'}
        reference=observer.finish(final)
        packet=read(path,{})['current']['receipt']
        assert packet['final_evidence']==final
        assert packet['deadline']==40
        assert packet['requires_live_refresh'] is True
        with pytest.raises(BlockingIOError):
            with Observation(path,PR,HEAD):pass
    assert reference['path']==str(path)
    assert path.stat().st_mode & 0o777==0o600


@pytest.mark.parametrize('status',['head_changed','failure','timeout','no_checks','error'])
def test_negative_terminal_receipts_never_become_success(tmp_path,status):
    path=tmp_path/'observation.json'
    with Observation(path,PR,HEAD,receipt=True) as observer:
        observed=result(status)
        observer.update(observed)
        observer.finish(observed)
    packet=read(path,{})['current']['receipt']
    assert packet['state']==status
    assert packet['final_evidence']['result']==status
    assert packet['final_evidence']['result']!='success'


def test_denied_access_stays_latched_and_old_success_is_not_reused(tmp_path):
    path=tmp_path/'observation.json'
    with Observation(path,PR,HEAD,receipt=True,clock=lambda:10) as observer:
        observer.update(result('success'));observer.finish(result('success'))
    deadline=read(path,{})['current']['deadline']
    with Observation(path,PR,HEAD,clock=lambda:20) as observer:
        assert observer.current['receipt']['state']=='observing'
        assert observer.current['receipt']['final_evidence'] is None
        observer.denied(PermissionError('denied'))
    assert read(path,{})['current']['receipt']['state']=='permission_denied'
    with pytest.raises(PermissionDenied):
        with Observation(path,PR,HEAD):pass
    with Observation(path,PR,HEAD,retry_denied=True) as observer:
        assert observer.current['deadline']==deadline
        assert observer.current['receipt']['state']=='observing'


def test_interrupt_and_changed_head_preserve_receipt_identity(tmp_path):
    path=tmp_path/'observation.json'
    with pytest.raises(KeyboardInterrupt):
        with Observation(path,PR,HEAD,receipt=True) as observer:
            observer.update(result())
            raise KeyboardInterrupt()
    old=read(path,{})['current']['receipt']
    assert old['state']=='interrupted'
    with Observation(path,PR,'b'*40,receipt=True) as observer:
        assert observer.current['receipt']['identity']['head']=='b'*40
        with pytest.raises(ValueError,match='terminal result'):
            observer.finish(result('success'))
    assert read(path,{})['history'][0]['receipt']==old
