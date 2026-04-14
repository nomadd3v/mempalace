# nomadd3v/mempalace — Fork Notes

This fork of [milla-jovovich/mempalace](https://github.com/milla-jovovich/mempalace) makes
two categories of change: a bug fix for legacy palace data, and a fundamental shift in
**what gets indexed and when**.

---

## Core concept change: Claude Code session logs as the palace

Upstream mempalace is designed to index project files — source code, docs, configs — and
optionally conversation exports you provide. This fork is built around a different primary
use case: **automatically indexing every Claude Code session as it ends**.

Claude Code writes a full JSONL transcript of every session to `~/.claude/projects/` —
every user message, assistant response, tool call, and result. These logs are the most
complete record of every decision made, every bug debugged, every architecture discussed,
every code change explained. They exist already, unconditionally, with zero extra effort.

This fork treats those logs as the palace's primary data source:

```
Claude Code session ends
        │
        ▼
~/.claude/projects/<session>.jsonl written
        │
        ▼ (WatchPaths trigger)
launchd fires: mempalace mine ~/.claude/projects --mode convos
        │
        ▼
New session chunks embedded + deduplicated into palace
        │
        ▼
Future sessions can semantically search all past work:
  "why did we switch the order lookup to lazy fetch?"
  "what was the Supabase schema change for the scaffold?"
  "show me the CoreML fix we applied to chromadb"
```

This is a different value proposition from upstream's project-file indexing. The palace
becomes a persistent memory layer for Claude Code itself — verbatim recall of past
sessions without requiring any manual export, annotation, or curation. The AI never wrote
the logs; it just has to mine them.

**What `--mode convos` does with JSONL transcripts:**

Claude Code JSONL files contain structured message objects with `role` (user/assistant),
`content`, and tool call/result blocks. `mine --mode convos` parses these into text chunks
suitable for embedding — stripping tool internals, keeping the meaningful content — and
deduplicates against what is already in the palace so re-running is always safe.

**What the palace does not replace:**

The structured markdown memory files (`~/.claude/projects/memory/`) that Claude writes
via the Stop hook are still valuable. They are synthesized summaries — the AI's own
distillation of what matters. The palace has verbatim recall; the markdown notes have
judgment. Both are useful; they serve different query modes.

---

## Changes vs upstream

### 1. `mempalace/compat.py` (new) + `mempalace/__init__.py`

**Problem:** Palaces built with chromadb < 0.5 serialise the HNSW vector index metadata
(`PersistentData`) via `pickle` as a plain `dict`. chromadb 0.6.x deserialises the file
correctly but then accesses the result via attribute notation — `obj.dimensionality`,
`obj.id_to_label`, etc. — which raises `AttributeError: 'dict' object has no attribute
'dimensionality'`, making the entire palace unreadable.

The failure surface is any operation that opens the vector segment for querying or
mutation: `search`, `mine`, `repair`, `wake-up`. Every mempalace command that touches
stored embeddings is broken until this is fixed.

**Fix:** `compat.py` monkey-patches `PersistentData.load_from_file` at import time to
detect a dict payload and promote it back to a `PersistentData` instance:

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

Applied via `apply_all()` called from `__init__.py` so it is in effect for every
mempalace operation without any user action. No migration or `repair` run required.
Existing palaces with 100K+ drawers continue to work without data loss.

---

### 2. `hooks/mempal_save_hook.sh`

**Problem (mining):** The upstream hook fires every 15 exchanges, blocks the AI from
stopping, and optionally runs `mempalace mine` in the background on the same stop cycle.
In practice `MEMPAL_DIR` was empty (mining was disabled), but the 15-exchange frequency
still interrupted sessions constantly — the AI had to pause and write memory notes far
more often than necessary, spending tokens on overhead rather than work.

A deeper issue: running `mine` inside a session hook ties indexing latency directly to
session responsiveness. A large `mine` run (1,300+ JSONL files) blocks or delays the
session and competes with it for CPU.

**Fix:** Mining is fully decoupled from the hook and moved to a launchd agent
(`com.nomad.mempalace-mine`) that watches `~/.claude/projects/` with `WatchPaths` and
a 5-minute `ThrottleInterval`. The launchd agent runs `mine --mode convos` automatically
after any session that writes new transcripts, completely independent of the session
lifecycle. See [launchd setup](#launchd-agent-macos) below.

With mining removed from the hook, the save interval is raised from 15 → 45 exchanges.
Raw transcript content is captured by launchd regardless; the hook's only remaining job
is prompting the AI to write structured markdown memory notes, which does not need to
happen every 15 messages.

**Problem (agentic interruption):** The upstream hook fires even mid-tool-use, interrupting
the AI while it is in the middle of a multi-step agentic task. This is both annoying and
incorrect — the AI cannot meaningfully save memory state while tools are in flight.

**Fix:** The hook now reads `stop_reason` from the transcript and exits with `{}` (let
through) when `stop_reason == "tool_use"`.

**Problem (venv):** The hook invoked the system `python3`, which does not have mempalace
installed after the move to an isolated venv.

**Fix:** A `MEMPAL_PYTHON` variable points to the venv interpreter
(`$HOME/.mempalace/venv/bin/python3`). All Python invocations in the hook use it.

**Summary of hook changes:**

| | Upstream | This fork |
|---|---|---|
| Save interval | 15 exchanges | 45 exchanges |
| Mining | In-hook background job | launchd agent (fully decoupled) |
| Agentic task guard | None | Skips when `stop_reason == tool_use` |
| Python interpreter | System `python3` | `$MEMPAL_PYTHON` (venv) |
| Dashboard update | Included in reason string | Removed |

---

## Installation

```bash
# 1. Create an isolated venv with compatible chromadb
python3 -m venv ~/.mempalace/venv
~/.mempalace/venv/bin/pip install "chromadb>=0.5.0,<0.7"

# 2. Install this fork as an editable package
~/.mempalace/venv/bin/pip install -e /path/to/this/repo

# 3. Verify
~/.mempalace/venv/bin/python3 -m mempalace status
~/.mempalace/venv/bin/python3 -m mempalace search "test query"
```

**Why `chromadb<0.7`?** The HNSW persistence layer changed significantly in chromadb 1.x.
The 0.6.x API is what mempalace 3.x targets, and the dict-pickle fix in this fork is
written against that version's `PersistentData` constructor signature.

---

## launchd agent (macOS)

Create `~/Library/LaunchAgents/com.nomad.mempalace-mine.plist`:

```xml
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>com.nomad.mempalace-mine</string>
    <key>ProgramArguments</key>
    <array>
        <string>/Users/nomad/.mempalace/venv/bin/python3</string>
        <string>-m</string>
        <string>mempalace</string>
        <string>mine</string>
        <string>/Users/nomad/.claude/projects</string>
        <string>--mode</string>
        <string>convos</string>
    </array>
    <key>WatchPaths</key>
    <array>
        <string>/Users/nomad/.claude/projects</string>
    </array>
    <key>ThrottleInterval</key>
    <integer>300</integer>
    <key>StandardOutPath</key>
    <string>/Users/nomad/.mempalace/hook_state/mine.log</string>
    <key>StandardErrorPath</key>
    <string>/Users/nomad/.mempalace/hook_state/mine.log</string>
    <key>RunAtLoad</key>
    <false/>
</dict>
</plist>
```

```bash
launchctl load ~/Library/LaunchAgents/com.nomad.mempalace-mine.plist
```

The agent fires automatically after any Claude Code session ends and writes new transcripts.
Indexing is throttled to at most once every 5 minutes so rapid consecutive sessions do not
stack up multiple mine processes.

---

## MCP server (Claude Code)

```bash
claude mcp add --scope user mempalace -- \
  /Users/nomad/.mempalace/venv/bin/python3 -m mempalace.mcp_server
```

Registers mempalace as a user-scope MCP tool available in all Claude Code sessions.
