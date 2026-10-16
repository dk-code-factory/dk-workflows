import importlib.util
from pathlib import Path
import sys
from unittest.mock import patch

import pytest


SCRIPT = Path(__file__).resolve().parents[1] / 'scripts' / 'pr_edit_guard.py'
spec = importlib.util.spec_from_file_location('pr_edit_guard', SCRIPT)
guard = importlib.util.module_from_spec(spec)
spec.loader.exec_module(guard)


def test_edit_requires_full_pr_url(tmp_path):
    title = tmp_path / 'title.txt'
    body = tmp_path / 'body.txt'
    title.write_text('Updated title')
    body.write_text('Updated body')
    prefix = ['pr_edit_guard.py', '--review-state', 'complete',
              '--title-file', str(title), '--body-file', str(body)]
    with patch.object(guard, 'require_complete'), patch.object(guard.subprocess, 'run') as run:
        with patch.object(sys, 'argv', prefix + ['7']):
            with pytest.raises(ValueError, match='full PR URL'):
                guard.main()
        run.assert_not_called()
        url = 'https://github.com/upstream/repo/pull/7'
        with patch.object(sys, 'argv', prefix + [url]):
            guard.main()
        assert run.call_args.args[0][:4] == ['gh', 'pr', 'edit', url]
