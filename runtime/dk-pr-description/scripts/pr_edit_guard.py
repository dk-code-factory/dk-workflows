#!/usr/bin/env python3
"""Edit a PR title and body only after the description review loop completes."""
import sys
from pathlib import Path
sys.path.insert(0, str(next(p for p in Path(__file__).resolve().parents if (p / 'shared').is_dir())))
import argparse
from pathlib import Path
import subprocess
import sys
from urllib.parse import urlparse

sys.path.insert(0, str(next(p for p in Path(__file__).resolve().parents if (p / 'shared').is_dir())))
from shared.review_loop import require_complete


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--review-state', required=True)
    parser.add_argument('--title-file', type=Path, required=True)
    parser.add_argument('--body-file', type=Path, required=True)
    parser.add_argument('pr', help='full PR URL')
    args = parser.parse_args()
    require_complete(args.review_state, 'dk-pr-description')
    title = args.title_file.read_text().strip()
    if not title or '\n' in title or '\r' in title or not args.body_file.is_file():
        raise ValueError('a single-line title and body file are required')
    parsed = urlparse(args.pr)
    parts = parsed.path.strip('/').split('/')
    if parsed.scheme != 'https' or not parsed.hostname or len(parts) != 4 or parts[2] != 'pull' or not parts[3].isdigit():
        raise ValueError('a full PR URL is required')
    command = ['gh', 'pr', 'edit', args.pr]
    command += ['--title', title, '--body-file', str(args.body_file)]
    subprocess.run(command, check=True)


if __name__ == '__main__':
    try:
        main()
    except (ValueError, OSError, subprocess.CalledProcessError) as exc:
        print(f'pr edit: {exc}', file=sys.stderr)
        sys.exit(1)
