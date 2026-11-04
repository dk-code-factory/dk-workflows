"""CI deadlines/attempt evidence survive interruption independently of workflows."""
import os
from pathlib import Path

import pytest
from shared.ci_observation import Observation, PermissionDenied, for_pr
from shared.private_state import read

PR='https://github.com/owner/repo/pull/7'
HEAD='a'*40


def test_interruption_keeps_deadline_and_one_extension(tmp_path):
    path=tmp_path/'observation.json';clock=[10]
    def now():return clock[0]
    pending={'head':HEAD,'result':'pending','checks':[{'name':'test','link':'attempt-1'}]}
    with Observation(path,PR,HEAD,timeout=30,extension=30,clock=now) as observer:
        assert observer.update(pending)['deadline']==40
    clock[0]=45
    with Observation(path,PR,HEAD,timeout=900,extension=900,clock=now) as observer:
        first=observer.update(pending)
        assert first['deadline']==70 and first['extensions']==1
    clock[0]=100
    with Observation(path,PR,HEAD,clock=now) as observer:
        last=observer.update(pending)
        assert last['remaining_seconds']==0 and last['extensions']==1


def test_denied_access_and_concurrent_observer_require_explicit_recovery(tmp_path):
    path=tmp_path/'observation.json'
    with Observation(path,PR,HEAD) as observer:
        with pytest.raises(BlockingIOError):
            with Observation(path,PR,HEAD):pass
        observer.denied(PermissionError('access denied'))
    with pytest.raises(PermissionDenied):
        with Observation(path,PR,HEAD):pass
    with Observation(path,PR,HEAD,retry_denied=True) as observer:
        assert 'permission_denied' not in observer.current


def test_new_head_gets_own_window_and_returning_head_does_not_reset(tmp_path):
    path=tmp_path/'observation.json'
    with Observation(path,PR,HEAD,clock=lambda:10):pass
    with Observation(path,PR,'b'*40,clock=lambda:20):pass
    with Observation(path,PR,HEAD,clock=lambda:30) as observer:
        assert observer.current['started']==10


def test_ci_observation_uses_private_state_home():
    current=for_pr(PR,HEAD)
    assert str(current.path).startswith(os.environ['XDG_STATE_HOME'])
    assert 'dk-pr-skills/v2/ci' in str(current.path)
