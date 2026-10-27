import importlib.util
import json
import subprocess
from pathlib import Path
import sys
from unittest.mock import patch

import pytest


SCRIPT = Path(__file__).resolve().parents[1] / 'scripts' / 'pr_edit_guard.py'
spec = importlib.util.spec_from_file_location('pr_edit_guard', SCRIPT)
guard = importlib.util.module_from_spec(spec)
spec.loader.exec_module(guard)


def test_edit_requires_full_pr_url(tmp_path, monkeypatch):
    monkeypatch.setenv('XDG_STATE_HOME', str(tmp_path / 'state'))
    title = tmp_path / 'title.txt'
    body = tmp_path / 'body.txt'
    title.write_text('Updated title')
    body.write_text('Updated body')
    prefix = ['pr_edit_guard.py', '--review-state', 'complete',
              '--title-file', str(title), '--body-file', str(body)]
    url = 'https://github.com/upstream/repo/pull/7'
    live = {'url': url, 'headRefOid': 'a' * 40, 'title': 'Old title', 'body': 'Old body'}
    real_run = subprocess.run
    def github(argv, **kwargs):
        if argv[0] != 'gh':
            return real_run(argv, **kwargs)
        if argv[1:3] == ['pr', 'view']:
            return subprocess.CompletedProcess(argv, 0, json.dumps(live), '')
        assert argv[:4] == ['gh', 'pr', 'edit', url]
        live.update(title=argv[argv.index('--title') + 1],
                    body=Path(argv[argv.index('--body-file') + 1]).read_text())
        return subprocess.CompletedProcess(argv, 0, '', '')
    with patch.object(guard, 'require_complete'), patch.object(guard.subprocess, 'run', side_effect=github) as run:
        with patch.object(sys, 'argv', prefix + ['7']):
            with pytest.raises(ValueError, match='full PR URL'):
                guard.main()
        run.assert_not_called()
        with patch.object(sys, 'argv', prefix + [url]):
            guard.main()
        edits = [c.args[0] for c in run.call_args_list if c.args[0][:3] == ['gh', 'pr', 'edit']]
        assert len(edits) == 1 and edits[0][:4] == ['gh', 'pr', 'edit', url]
        assert live['title'] == 'Updated title' and live['body'] == 'Updated body'
