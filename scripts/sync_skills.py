#!/usr/bin/env python3
"""Copy maintained skill prompts, defaults and schemas into Claude's distribution."""
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def main():
    for directory in sorted((ROOT/'codex/skills').iterdir()):
        if not directory.is_dir():
            continue
        for name in ('SKILL.md', 'config.json', 'config.schema.json'):
            source = directory/name
            if source.is_file():
                target = ROOT/'claude/skills'/directory.name/name
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(source.read_bytes())


if __name__ == '__main__':
    main()
