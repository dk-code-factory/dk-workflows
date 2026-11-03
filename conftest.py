"""Every standalone package test uses private temporary state."""
import subprocess
from pathlib import Path
import pytest

@pytest.fixture(autouse=True)
def private_state(tmp_path, monkeypatch):
    for key, folder in [('XDG_STATE_HOME','state'),('XDG_DATA_HOME','data'),('XDG_CONFIG_HOME','config')]:
        monkeypatch.setenv(key,str(tmp_path/folder))
    original=subprocess.run
    def guarded(argv,*args,**kwargs):
        if isinstance(argv,(list,tuple)) and argv and Path(argv[0]).name in {'gh','codex','claude'}:
            raise AssertionError('Unmocked network or model call in offline test')
        return original(argv,*args,**kwargs)
    monkeypatch.setattr(subprocess,'run',guarded)
