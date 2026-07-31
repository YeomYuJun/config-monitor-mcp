# Config-Monitor MCPAPPS

> See, edit, and roll back your Claude configuration — global and per-project — from a single screen.

<img src="/assets/img/fullscreen.png" width="640" alt="config-monitor dashboard">

## About This Project

**config-monitor** is a Model Context Protocol (MCP) App — an interactive HTML dashboard that renders directly inside Claude Desktop — for inspecting and managing every Claude setting on your machine. It brings Claude Code (`.claude.json`, `settings.json`), Claude Desktop (`claude_desktop_config.json`), and per-project `.claude/` folders into one place.

As you accumulate skills, MCP servers, hooks, and agents — plus a separate `.claude` folder for each project — those settings scatter across files that live in different locations and follow different rules. It becomes hard to answer simple questions like *what is actually applied right now, and where does it come from?*

Every change is reversible by design. Edits take an automatic snapshot before they run, and overwrites or deletes are backed up first (`.bak` for files, `.trash` for folders), so you can always get the previous state back.

## Table of Contents

- [About This Project](#about-this-project)
- [Features](#features)
- [Requirements](#requirements)
- [Setup](#setup)
- [Usage](#usage)
  - [Header](#header)
  - [Tracked Files](#tracked-files)
  - [Config](#config)
  - [Library](#library)
  - [History / Diff](#history--diff)
  - [Safety](#safety)
- [What It Reads](#what-it-reads)
- [Troubleshooting](#troubleshooting)
- [Notes](#notes)

## Features

- **One view across sources** — Claude Code, Claude Desktop, and each tracked project side by side, with scope badges (`global` / `project`).
- **Snapshots & diffs** — track any config file, browse its snapshot timeline, compare two versions, and restore an earlier one.
- **Direct editing** — add or remove `allow` / `deny` / `ask` permissions, hooks, and MCP servers; scaffold or remove skills and agents.
- **Library install** — install/remove a local library (agents / commands / skills) into the global config or a specific project. Additive, not an overwrite, so existing settings stay intact.
- **Override badges** — when two items share a name, the one that is *not* actually applied is flagged, following the real precedence rules (project wins for agents, global wins for skills).
- **Reversible by default** — auto-snapshot before every edit; `.bak` / `.trash` backups before every overwrite or delete.

## Requirements

- **Node.js** (LTS) — verify with `node -v`
- **Python 3.10+** on `PATH` — verify with `python --version`
- **Windows** with **Claude Desktop** — the widget probes Windows desktop config paths and the file watcher runs on PowerShell.

## Setup

Configure Claude Desktop to launch the server from its `claude_desktop_config.json`. Install and build first, then register the MCP entry and restart Claude Desktop.

**1. Install and build** — from the project folder:

```bash
npm install
npm run build
```

**2. Register the MCP server** — open your `claude_desktop_config.json` and add an entry under `mcpServers`:

```json
"config-monitor": {
  "command": "npx",
  "args": ["tsx", "C:/tools/config-monitor/src/server-stdio.ts"],
  "env": {
    "CLAUDE_SNAPSHOT_STORE": "C:/Users/<you>/.claude-snapshot"
  }
}
```

- `args` path → your unzipped folder's `src/server-stdio.ts`.
- `CLAUDE_SNAPSHOT_STORE` → where snapshots are stored (must be an existing drive; defaults to `D:\.claude-snapshot` if unset).
- *(optional)* to use a library, add `"CLAUDE_CONFIG_LIBRARIES": "C:/.../my-library/.claude"` to `env`.

> **Config file location varies by install type.** Standard installs use `%APPDATA%\Claude\claude_desktop_config.json`; Microsoft Store / MSIX installs use `%LOCALAPPDATA%\Packages\Claude_*\LocalCache\Roaming\Claude\claude_desktop_config.json`. The most recently modified one is the config your running Claude reads.

**3. Restart Claude Desktop**, then call up the config-monitor widget.

## Usage

### Header

<img src="/assets/img/header.png" width="640" alt="Header controls">

The toolbar runs the global actions: **watcher** (a resident file watcher that auto-snapshots on change), **snapshot** (capture the current state once), and **refresh** (re-read tracking, config, and library).

**Report** builds a static, data-baked HTML page and opens it in the browser for read-only/offline viewing; **Open in browser** opens the live dashboard in a tab. **Display settings** control the accent color, source-path visibility, and card description line count, alongside **KO / EN** language and **fullscreen** toggles.

### Tracked Files

<img src="/assets/img/tracked-files.png" width="560" alt="Tracked files panel">

The list of config files under snapshot watch, each showing a status badge (`new` / `modified` / `deleted` / `same`), a scope badge, and its path. Three global files (`~/.claude.json`, `~/.claude/settings.json`, and the desktop config) are tracked automatically when present.

**Add tracking** by entering a project folder, a `.claude` folder, or a file path — a folder is resolved to its `settings.json` / `settings.local.json`. **+ From project** one-click-tracks any Claude Code project that has a `.claude` folder. Clicking a row opens the history / diff panel on the right; project rows can be untracked with **✕** (this only removes them from the watch list — the file itself stays).

### Config

<img src="/assets/img/settings-section.png" width="560" alt="Config panel">

Cards for each category — MCP Servers, Claude Code (`.claude.json`), Permissions, Hooks, Skills, Agents, Scheduled Tasks, and Desktop Skills. Items are grouped by source (global expanded, per-project collapsed), with a `global N · project M` summary where projects contribute.

**Override badges** mark items that share a name but are *not* actually applied, with a dashed border and an amber tag. Precedence runs opposite ways: for **Agents** the project wins, so the **global** card is badged; for **Skills** the global (personal) config wins, so the **project** card is badged.

You can filter instantly with the scope chips, then **edit in place**: add/remove permission rules and hooks, add/remove MCP servers, and scaffold or remove skills and agents. Project-sourced items are view-only.

### Library

<img src="/assets/img/library-section.png" width="600" alt="Library panel">

A library is any directory shaped like `.claude` (with `agents/`, `commands/`, and/or `skills/`). This panel installs its items into a real config, with a status badge per item: `not installed`, `installed`, or `changed` (the library was updated and can be synced) — compared by **content hash**, not by name.

Pick an **install target** (`global (~/.claude)` or a tracked project), then install items individually, in bulk, or by skills-tree group. Each item offers **install** / **sync** (backup before overwrite) / **remove** (move to `.trash`). Library paths marked `ENV` come from `CLAUDE_CONFIG_LIBRARIES` and can't be removed from the dashboard.

### History / Diff

<img src="/assets/img/right-pannel.png" width="440" alt="History and diff panel">

Opens when you click a tracked-file row. It shows the snapshot timeline (time, message, hash), the diff between two selected versions, and the current file contents (read-only).

**Restore** rolls the file back to a chosen version. Because the current state is auto-snapshotted (plus a `.bak`) before restoring, you can undo the undo.

### Safety

- An automatic snapshot is taken before every edit, install, and restore.
- Before an overwrite, files are kept as `.bak` and directories are moved to `.trash`.
- Removal is a move to `.trash`, not a real delete — it can be recovered.
- Untracking only removes an entry from the watch list; the file is left in place.

<img src="/assets/img/fullscreen.png" width="720" alt="Fullscreen dashboard">

## What It Reads

The dashboard does not dump whole files — it extracts only the fields it needs and turns them into cards. `~/.claude.json` is read selectively (never conversation history), `settings.json` is parsed only for `permissions` and `hooks`, and frontmatter is read shallowly (top-level one-line `key: value` pairs).

<details>
<summary>Parsing scope per section</summary>

| Section | File read | Extracted into cards |
|---|---|---|
| MCP Servers (desktop) | `<Desktop>/claude_desktop_config.json` | per-server `command`, `args`, `env` **key names only** |
| Claude Code | `~/.claude.json` | global summary + global `mcpServers` (`command`/`args`) + project cards (path, allowedTools count, mcpServers names, trust) |
| Permissions | `~/.claude/settings.json` | `permissions.allow` / `deny` / `ask` rules |
| Hooks | `~/.claude/settings.json` | matcher count + command list per `hooks.<event>` |
| Skills (code) | `~/.claude/skills/` | immediate subfolders; `SKILL.md` `description` |
| Agents | `~/.claude/agents/` | frontmatter `name`, `description`, `tools` |
| Scheduled Tasks | `~/Claude/Scheduled/*/SKILL.md` | `description`, `cron`/`schedule`/`fireAt` |
| Desktop Skills | `<Desktop>/.../skills-plugin/**/manifest.json` | `description`, `creatorType`, `enabled`, `updatedAt` |

When a project is tracked, the Permissions / Hooks / Skills / Agents sections also read that project's `.claude/{settings.json,settings.local.json}`, `.claude/skills/`, and `.claude/agents/` and append them as project items.

</details>

<details>
<summary>Known limits</summary>

- `settings.json` and `settings.local.json` are **both** read, but shown as separate cards (not merged), each labeled with its source.
- `skills` is read one level deep; `agents` and `commands` recurse into subfolders (nested items are view-only).
- Project cards are capped at 20.
- Long values are truncated — descriptions at 600 chars, everything else at 160.
- Only what appears as a card is editable; keys that aren't parsed can't be changed from the dashboard.

</details>

## Troubleshooting

- **Widget doesn't appear** → run `npm run build` again.
- **`python` not found** (only `py` works) → add `"CONFIG_MONITOR_PYTHON": "py"` (or the full `python.exe` path) to the `env` block.
- **Snapshot / restore errors** → check the `CLAUDE_SNAPSHOT_STORE` path (defaults to `D:\.claude-snapshot`).
- **Watcher won't toggle** → run `Set-ExecutionPolicy -Scope CurrentUser RemoteSigned` once.

## Notes

Precedence and path-coverage details are summarized from the official Claude Code docs (skills, sub-agents, settings, memory, hooks, MCP) and may shift between versions — if behavior differs, defer to the docs. Local-config editing is intentionally constrained today to preserve structural integrity, and broader per-project editing is planned.
