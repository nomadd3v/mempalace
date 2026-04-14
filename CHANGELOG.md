# Changelog — nomadd3v/mempalace

All notable changes in this fork relative to
[milla-jovovich/mempalace](https://github.com/milla-jovovich/mempalace).

---

## [Unreleased] — 2026-04-14

### Added

#### `hooks/mine_dispatch.py` — Wing-aware session mining
Replaces the flat `mine ~/.claude/projects --mode convos` invocation in the
launchd agent with a project-aware dispatch layer.

- Reads `~/.mempalace/project_map.json` to map each `~/.claude/projects/`
  subdirectory to a named palace wing using longest-substring pattern matching.
- Calls `mine_convos` in-process once per directory with the correct `--wing`
  flag — no subprocess overhead, no duplicate chromadb client startups.
- Rooms within each wing are still auto-detected per chunk from conversation
  content (`decisions`, `code`, `problems`, `general`).
- Supports `--status` (preview wing assignments), `--dry-run`, and bare (full run).
- Uses mempalace's built-in `file_already_mined` dedup — re-running is always safe,
  already-indexed files are skipped without re-embedding.

**Why this matters:** Upstream mines project files you point at manually. This
fork mines Claude Code session logs automatically. Without wing dispatch, all
44+ project-specific transcript directories land in the same flat wing, making
wing-scoped search and `wake-up --wing <project>` meaningless. With dispatch,
sessions are routed to their project family at index time.

#### `hooks/project_map.json` — Wing assignment config
Declarative mapping of directory name patterns to wing names. Ships with three
wings: `betterpostools` (POS suite + all related apps), `nomadd3v` (personal
projects), `general` (default fallback).

To add a new project: add one `{"match": "...", "wing": "..."}` entry. No code
changes required. Longest-match wins, so specific patterns can override broad ones.

```json
{
  "default_wing": "general",
  "patterns": [
    {"match": "betterpostools", "wing": "betterpostools"},
    {"match": "db-suite",       "wing": "betterpostools"},
    {"match": "nomadd3v",       "wing": "nomadd3v"},
    {"match": "sudochef",       "wing": "nomadd3v"}
  ]
}
```

---

## [4-commit baseline] — 2026-04-14

The four commits that define this fork from upstream:

### `74ae124` — fix(compat): patch chromadb PersistentData.load_from_file for legacy dict pickles

**Problem:** Palaces built with chromadb < 0.5 serialized the HNSW vector index
metadata (`PersistentData`) as a plain `dict` via pickle. chromadb 0.6.x deserializes
correctly but then accesses the result via attribute notation (`obj.dimensionality`,
`obj.id_to_label`, etc.), raising `AttributeError: 'dict' object has no attribute
'dimensionality'`. Every mempalace operation that touches stored embeddings — `search`,
`mine`, `repair`, `wake-up` — fails until this is fixed.

**Impact:** A palace with 165,000+ drawers built on an older chromadb version is
completely unreadable under 0.6.x without this patch.

**Fix:** `mempalace/compat.py` — monkey-patches `PersistentData.load_from_file` at
import time. Detects a dict payload and promotes it back to a `PersistentData` instance
using the 0.6.x constructor signature:

```python
if isinstance(ret, dict):
    ret = PersistentData(
        dimensionality=ret.get("dimensionality"),
        total_elements_added=ret.get("total_elements_added", 0),
        id_to_label=ret.get("id_to_label", {}),
        label_to_id=ret.get("label_to_id", {}),
        id_to_seq_id=ret.get("id_to_seq_id", {}),
    )
```

`apply_all()` is called from `mempalace/__init__.py` so the patch is in effect for
every mempalace operation without user action. No migration, no `repair`, no data loss.

**Files changed:** `mempalace/compat.py` (new), `mempalace/__init__.py` (import + call)

---

### `dd777fb` — fix(hooks): decouple mining from session hook + venv python + agentic guard

Three independent fixes bundled into the save hook rewrite:

**1. Mining decoupled from the session lifecycle**

Previously the hook ran `mempalace mine` as a background job on the same stop cycle
that blocks the AI for memory saving. This tied indexing latency directly to session
responsiveness: a large mine run (1,300+ JSONL files) competed with the live session
for CPU, and errors in mine could interfere with the save cycle.

Fix: mining is removed from the hook entirely and moved to a dedicated launchd agent
(`com.nomad.mempalace-mine`) that watches `~/.claude/projects/` via `WatchPaths` with
a 5-minute `ThrottleInterval`. Indexing now runs completely outside the session
lifecycle — zero impact on session latency, no stacking under rapid consecutive sessions.
The `MEMPAL_DIR` variable is removed from the hook config; mining is no longer
a hook concern.

**2. Save interval raised 15 → 45 exchanges**

With mining decoupled, the hook's only remaining job is prompting the AI to write
structured markdown memory notes. Raw transcript content is captured by launchd
regardless of hook frequency — the hook firing more often than necessary just burns
tokens on overhead without adding coverage.

**3. Agentic task guard**

The hook previously fired during mid-task tool use, interrupting the AI in the middle
of a multi-step agentic operation. The hook now reads `stop_reason` from the JSONL
transcript and exits `{}` (let through) when `stop_reason == "tool_use"`. Saves only
happen when the AI has reached a genuine stopping point.

**4. Venv Python**

Hook now uses `MEMPAL_PYTHON="$HOME/.mempalace/venv/bin/python3"` for all Python
invocations. The previous system `python3` has a different chromadb version installed
and does not have mempalace in its site-packages.

**5. Dashboard update removed**

The save reason string previously included a dashboard update instruction.
Dashboard app has drifted to a different use case; removed from the prompt.

**Files changed:** `hooks/mempal_save_hook.sh` (rewrite)

---

### `cbf39c7` — docs(fork): document Claude Code session log indexing as primary use case

**`FORK.md`** — New file. Comprehensive documentation of why this fork exists and what
it changes, organized for a reader coming from upstream mempalace:

- **Core concept section:** Explains the fundamental shift — upstream indexes project
  files you point at manually; this fork indexes Claude Code's own JSONL session
  transcripts automatically via launchd. Includes architecture diagram showing the
  full pipeline from session end → WatchPaths trigger → mine → palace → semantic search.
- **Upstream comparison table:** Side-by-side of save interval, mining trigger, agentic
  guard, Python interpreter, dashboard update between upstream and this fork.
- **Installation instructions:** venv setup, editable install, verification commands.
- **launchd plist:** Full XML with comments, load command.
- **MCP registration:** One-liner to register mempalace as a user-scope Claude Code tool.
- **Why `chromadb<0.7`:** Explains the version constraint and what changes in 1.x.

---

### `f82d55e` — feat(dispatch): wing-aware mine dispatch for curated palace structure

See the **[Unreleased]** section above for full detail.

---

## Divergence from upstream

This fork does not intend to merge back. The changes are orthogonal to upstream's goals:
upstream is a general-purpose memory tool for project files; this fork is specialized for
indexing Claude Code session logs. The compat fix (`74ae124`) could theoretically be
upstreamed but depends on the upstream maintainer's chromadb version targets.

**Upstream tracking:** `upstream` remote points to `milla-jovovich/mempalace`. To pull
upstream improvements that don't conflict:

```bash
git fetch upstream
git merge upstream/main --no-ff
```
