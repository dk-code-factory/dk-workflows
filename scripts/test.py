#!/usr/bin/env python3
"""Run the bundled workflow, PR-domain and full-recipe suites offline."""
import argparse
import os
from pathlib import Path
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]


def main(argv=()):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--workers', type=int, default=0,
                        help='Parallel workers for recipe scenarios; zero runs serially')
    args = parser.parse_args(argv)
    if args.workers < 0:
        parser.error('--workers must be nonnegative')
    suites = [sorted((ROOT/'tests').glob('test_*.py')), [ROOT/'tests/pr']]
    recipe = ROOT/'tests/test_recipe_integration.py'
    if args.workers and recipe in suites[0]:
        suites[0].remove(recipe)
        suites.insert(1, [recipe])
    suites += [[path] for path in sorted(ROOT.glob('runtime/*/tests'))
               if any(path.rglob('test_*.py'))]
    env = {**os.environ, 'PYTHONDONTWRITEBYTECODE':'1',
           'PYTHONPATH':os.pathsep.join([str(ROOT/'runtime'), str(ROOT/'runtime/dk-pr-feedback-address/scripts')])}
    with tempfile.TemporaryDirectory(prefix='dk-workflows-tests-') as temporary:
        for index, suite in enumerate(suites):
            command = [sys.executable, '-m', 'pytest', '-q', *map(str, suite), '--tb=short',
                       '--durations=20',
                       '--basetemp', str(Path(temporary)/str(index)), '-o',
                       'cache_dir='+str(Path(temporary)/'cache')]
            if args.workers and suite == [recipe]:
                command += ['-n', str(args.workers), '--dist', 'worksteal']
            result = subprocess.run(command, cwd=ROOT, env=env)
            if result.returncode:
                raise SystemExit(result.returncode)


if __name__ == '__main__':
    main(sys.argv[1:])
