#!/usr/bin/env python3
"""Copy maintained Codex skill prompts into the Claude distribution."""
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def main():
    for source in sorted((ROOT/'codex/skills').glob('*/SKILL.md')):
        target = ROOT/'claude/skills'/source.parent.name/'SKILL.md'
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(source.read_bytes())


if __name__ == '__main__':
    main()
