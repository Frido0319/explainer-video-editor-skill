"""Command line entry point for the public video editor runtime."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .builder import build
from .manifest import load_project
from .verify import verify_project


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("validate", "build", "verify"))
    parser.add_argument("manifest", type=Path, nargs="?", default=Path("project.json"))
    args = parser.parse_args()
    try:
        project = load_project(args.manifest)
        if args.command == "validate":
            print(
                f"清单验证通过：mode={project['mode']} theme={project.get('theme', 'none')} duration={project['duration']}s"
            )
        elif args.command == "build":
            print(f"成片输出：{build(project)}")
        else:
            print(json.dumps(verify_project(project), ensure_ascii=False, indent=2))
    except Exception as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        raise SystemExit(1) from None


if __name__ == "__main__":
    main()
