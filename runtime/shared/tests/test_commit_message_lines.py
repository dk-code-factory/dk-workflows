import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

SCRIPT = Path(__file__).resolve().parents[1] / 'commit_message_lines.py'


def run(*args, cwd=None):
    return subprocess.run(args, cwd=cwd, text=True, capture_output=True,
                          env=dict(os.environ, PYTHONDONTWRITEBYTECODE='1'))


class CommitMessageLinesTest(unittest.TestCase):
    def test_file_checks_subject_body_trailer_and_trailing_spaces(self):
        with tempfile.TemporaryDirectory() as tmp:
            message = Path(tmp) / 'message.txt'
            message.write_text('s' * 80 + '\n\n' + 'b' * 80 + '\n')
            self.assertEqual(run(sys.executable, str(SCRIPT), '--file', str(message)).returncode, 0)
            message.write_text('s' * 81 + '\n\n' + 'b' * 80 + ' \n' + 'Signed-off-by: ' + 'x' * 66 + '\n')
            failed = run(sys.executable, str(SCRIPT), '--file', str(message))
            self.assertEqual(failed.returncode, 1)
            self.assertIn('line 1 has 81 characters', failed.stderr)
            self.assertIn('line 3 has 81 characters', failed.stderr)
            self.assertIn('line 4 has 81 characters', failed.stderr)

    def test_branch_checks_every_commit_after_base(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            self.assertEqual(run('git', 'init', '-q', str(repo)).returncode, 0)
            for key, value in [('user.name', 'Tester'), ('user.email', 'tester@example.com')]:
                self.assertEqual(run('git', 'config', key, value, cwd=repo).returncode, 0)
            (repo / 'base').write_text('base')
            run('git', 'add', 'base', cwd=repo)
            run('git', 'commit', '-qm', 'base', cwd=repo)
            base = run('git', 'rev-parse', 'HEAD', cwd=repo).stdout.strip()
            (repo / 'first').write_text('first')
            run('git', 'add', 'first', cwd=repo)
            run('git', 'commit', '-qm', 'f' * 81, cwd=repo)
            first = run('git', 'rev-parse', 'HEAD', cwd=repo).stdout.strip()
            (repo / 'second').write_text('second')
            run('git', 'add', 'second', cwd=repo)
            run('git', 'commit', '-qm', 'second', '-m', 'b' * 81, cwd=repo)
            second = run('git', 'rev-parse', 'HEAD', cwd=repo).stdout.strip()
            failed = run(sys.executable, str(SCRIPT), '--base', base, '--cwd', str(repo))
            self.assertEqual(failed.returncode, 1)
            self.assertIn(f'{first[:12]}: line 1 has 81 characters', failed.stderr)
            self.assertIn(f'{second[:12]}: line 3 has 81 characters', failed.stderr)
            tree = run('git', 'rev-parse', 'HEAD^{tree}', cwd=repo).stdout.strip()
            unrelated = run('git', 'commit-tree', tree, '-m', 'unrelated', cwd=repo).stdout.strip()
            rejected = run(sys.executable, str(SCRIPT), '--base', unrelated, '--cwd', str(repo))
            self.assertEqual(rejected.returncode, 1)
            self.assertIn('base must be an ancestor of HEAD', rejected.stderr)


if __name__ == '__main__':
    unittest.main()
