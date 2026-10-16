import importlib.util
from pathlib import Path
from unittest.mock import patch


SCRIPT = Path(__file__).resolve().parents[1] / 'scripts' / 'report_pr_comments.py'
spec = importlib.util.spec_from_file_location('report_pr_comments_cli', SCRIPT)
report = importlib.util.module_from_spec(spec)
spec.loader.exec_module(report)


def test_report_uses_explicit_fork_pr_url(tmp_path):
    url = 'https://github.com/upstream/repo/pull/7'
    state = {'pr': {'url': url}, 'viewer': 'author'}
    path = tmp_path / 'cache.json'
    with patch.object(report.core, 'scan', return_value=(state, path)) as scan, \
         patch.object(report.core, 'render', return_value='report'), \
         patch.object(report.core, 'atomic_write'):
        report.main(pr=url)
    scan.assert_called_once_with(url)
