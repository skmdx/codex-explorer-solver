#!/usr/bin/env python3
"""Create-only installer. Does not modify config.toml, AGENTS.md, or Git state."""
from __future__ import annotations

import argparse
import json
import os
import sys
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parent


def install(repo: Path, *, apply: bool, model: str = "gemini-3.8-flash-medium", deep_model: str = "gemini-3.8-flash-high") -> list[str]:
    repo = repo.resolve(strict=True)
    if not repo.is_dir() or not (repo / ".git").exists():
        raise ValueError("repo must be a Git repository root (worktrees are supported)")
    payload = ROOT / "payload"
    plan: list[tuple[Path, bytes]] = []
    for source in sorted(payload.rglob("*")):
        if not source.is_file() or "__pycache__" in source.parts:
            continue
        relative = source.relative_to(payload)
        destination = repo / relative
        # Refuse symlinked destination parents rather than writing elsewhere.
        for parent in [destination, *destination.parents]:
            if parent == repo:
                break
            if parent.is_symlink():
                raise ValueError(f"destination contains a symlink: {parent}")
        if destination.exists():
            raise ValueError(f"refusing to overwrite existing file: {relative}")
        data = source.read_bytes()
        if source.name == "agy.toml":
            text = data.decode("utf-8")
            config = tomllib.loads(text)
            for key, value in (("explorer_model", model), ("reader_model", model), ("deep_model", deep_model)):
                text = text.replace(key + " = " + json.dumps(config[key]), key + " = " + json.dumps(value), 1)
            tomllib.loads(text)
            data = text.encode("utf-8")
        plan.append((destination, data))
    if apply:
        written: list[Path] = []
        try:
            for destination, data in plan:
                destination.parent.mkdir(parents=True, exist_ok=True)
                with destination.open("xb") as stream:
                    # Record only after the exclusive creation succeeds.
                    written.append(destination)
                    stream.write(data)
                os.chmod(destination, 0o644)
        except BaseException:
            for path in reversed(written):
                path.unlink(missing_ok=True)
            raise
    return [str(path.relative_to(repo)) for path, _ in plan]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", type=Path, required=True)
    parser.add_argument("--apply", action="store_true", help="without this flag, only show the plan")
    parser.add_argument("--model", default="gemini-3.8-flash-medium")
    parser.add_argument("--deep-model", default="gemini-3.8-flash-high", help="extra AGY exploration, not the Codex parent model")
    args = parser.parse_args()
    try:
        files = install(args.repo, apply=args.apply, model=args.model, deep_model=args.deep_model)
        print(json.dumps({"applied": args.apply, "files": files,
                          "existing_config_modified": False, "agents_md_modified": False}, indent=2))
        return 0
    except (OSError, ValueError) as exc:
        print(f"install error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
