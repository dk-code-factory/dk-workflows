#!/usr/bin/env python3
"""Optional host-neutral JSON startup hook; workflows also refresh explicitly."""
import json
from pathlib import Path
import subprocess
import sys

payload = json.load(sys.stdin)
root = Path(__file__).resolve().parent.parent
proc = subprocess.run([sys.executable, str(root / 'runtime/dk-config/scripts/repo_rules.py'),
                       '--cwd', payload.get('cwd', '.'), 'refresh'], text=True, capture_output=True)
if proc.returncode:
    result = {'systemMessage': 'Repository rule refresh failed: ' + proc.stderr.strip()}
else:
    result = {'hookSpecificOutput': {'hookEventName': payload.get('hook_event_name', 'SessionStart'),
                                    'additionalContext': proc.stdout.strip()}}
print(json.dumps(result))
