"""Every standalone package test uses private temporary state."""
import subprocess
import sys
from pathlib import Path
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent/'tests/fixtures'))

@pytest.fixture(autouse=True)
def private_state(tmp_path, monkeypatch):
    for key, folder in [('XDG_STATE_HOME','state'),('XDG_DATA_HOME','data'),('XDG_CONFIG_HOME','config')]:
        monkeypatch.setenv(key,str(tmp_path/folder))
    def guard(original):
        def guarded(argv,*args,**kwargs):
            if isinstance(argv,(list,tuple)) and argv and Path(argv[0]).name in {'gh','codex','claude'}:
                raise AssertionError('Unmocked network or model call in offline test')
            return original(argv,*args,**kwargs)
        return guarded
    for method in ('run','Popen'):
        monkeypatch.setattr(subprocess,method,guard(getattr(subprocess,method)))
