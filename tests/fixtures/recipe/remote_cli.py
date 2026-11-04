"""Offline executables for full recipe tests; unknown network operations fail."""
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys

state_path=Path(os.environ['DK_RECIPE_REMOTE'])
state=json.loads(state_path.read_text())
module=importlib.util.spec_from_file_location('fixture_remote',os.environ['DK_RECIPE_FIXTURE'])
fixture=importlib.util.module_from_spec(module);module.loader.exec_module(fixture)
remote=fixture.Remote(state)
try:
    if Path(sys.argv[0]).name=='git':
        arguments=sys.argv[1:]
        arguments=[os.environ['DK_RECIPE_BARE'] if arg=='https://github.com/owner/repo.git' else arg for arg in arguments]
        if any('://' in arg or arg.startswith('git@') for arg in arguments):
            raise AssertionError('Fixture attempted external Git access')
        result=subprocess.run([os.environ['DK_RECIPE_GIT'],*arguments],text=True,capture_output=True)
        if result.returncode:
            sys.stdout.write(result.stdout);sys.stderr.write(result.stderr);raise SystemExit(result.returncode)
        if 'push' in arguments:
            ref=arguments[-1]
            head,branch=ref.split(':refs/heads/')
            state['branches'][branch]=head
            if branch=='pr-branch':
                state['metadata']['headRefOid']=head
                state['run']['head_sha']=head
            remote.changed('push',None)
        sys.stdout.write(result.stdout)
    else:
        if sys.argv[1:3]==['pr','checks']:
            state.setdefault('check_reads',[]).append(state['metadata']['headRefOid'])
        payload=json.loads(sys.stdin.read()) if '--input' in sys.argv else None
        value=remote.run(['gh',*sys.argv[1:]],payload=payload)
        if isinstance(value,str):sys.stdout.write(value)
        else:print(json.dumps(value))
except (OSError,AssertionError,KeyError,ValueError) as exc:
    print(str(exc),file=sys.stderr);sys.exit(1)
finally:
    state_path.write_text(json.dumps(state))
