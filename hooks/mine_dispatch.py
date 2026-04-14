#!/usr/bin/env python3
"""
mine_dispatch.py — Route Claude Code transcript directories to palace wings.

Reads ~/.mempalace/project_map.json to map each ~/.claude/projects/ subdirectory
to a named palace wing, then calls mine_convos for each one in-process (no
subprocess overhead).

Rooms are auto-detected per chunk from conversation content (decisions, code,
problems, general, etc.) — we only control the wing assignment here.

Usage:
    python3 mine_dispatch.py              # mine all dirs, skip already-indexed
    python3 mine_dispatch.py --dry-run    # show what would be mined, no writes
    python3 mine_dispatch.py --status     # show wing assignments without mining

Called by launchd (com.nomad.mempalace-mine) on WatchPaths trigger.
"""

import os
import sys
import json
import logging
from pathlib import Path
from collections import defaultdict

PROJECTS_DIR = Path.home() / ".claude" / "projects"
MAP_FILE = Path.home() / ".mempalace" / "project_map.json"
LOG_FILE = Path.home() / ".mempalace" / "hook_state" / "mine.log"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(message)s",
    datefmt="%H:%M:%S",
    handlers=[
        logging.FileHandler(LOG_FILE),
        logging.StreamHandler(sys.stdout),
    ],
)
log = logging.getLogger("dispatch")


def load_map():
    if MAP_FILE.exists():
        with open(MAP_FILE) as f:
            data = json.load(f)
        return data.get("patterns", []), data.get("default_wing", "general")
    return [], "general"


def resolve_wing(dir_name: str, patterns: list, default: str) -> str:
    """Return the wing for a directory name. Longest substring match wins."""
    best_len = 0
    best_wing = default
    for entry in patterns:
        match = entry["match"]
        if match in dir_name and len(match) > best_len:
            best_len = len(match)
            best_wing = entry["wing"]
    return best_wing


def main():
    dry_run = "--dry-run" in sys.argv
    status_only = "--status" in sys.argv

    patterns, default_wing = load_map()

    if not PROJECTS_DIR.exists():
        log.error(f"Projects dir not found: {PROJECTS_DIR}")
        sys.exit(1)

    # Group subdirectories by wing
    wing_dirs = defaultdict(list)
    for d in sorted(PROJECTS_DIR.iterdir()):
        if not d.is_dir():
            continue
        wing = resolve_wing(d.name, patterns, default_wing)
        wing_dirs[wing].append(d)

    if status_only:
        for wing in sorted(wing_dirs):
            print(f"\n[{wing}]")
            for d in wing_dirs[wing]:
                print(f"  {d.name}")
        return

    # Import mempalace — must be on PYTHONPATH (editable install in venv)
    try:
        from mempalace.config import MempalaceConfig
        from mempalace.convo_miner import mine_convos
    except ImportError as e:
        log.error(f"Cannot import mempalace: {e}")
        log.error("Run this script with the venv Python: ~/.mempalace/venv/bin/python3")
        sys.exit(1)

    palace_path = MempalaceConfig().palace_path

    total_new = 0
    for wing in sorted(wing_dirs):
        dirs = wing_dirs[wing]
        log.info(f"Wing [{wing}]: {len(dirs)} directories")
        for d in dirs:
            try:
                # mine_convos handles dedup — files already indexed are skipped
                mine_convos(
                    convo_dir=str(d),
                    palace_path=palace_path,
                    wing=wing,
                    agent=None,
                    limit=0,
                    dry_run=dry_run,
                    extract_mode=None,
                )
            except Exception as e:
                log.error(f"  Error mining {d.name}: {e}")

    log.info("Dispatch complete.")


if __name__ == "__main__":
    main()
