---
name: kea
description: Work with the user's kea second brain, a folder of plain Markdown that holds verified facts with sources and dates (STATE.md), decisions with revisit dates, dated predictions that get scored, a roadmap, plans and a local search index over their past AI sessions, chat exports, git history and notes. Use when the user mentions their brain, second brain, STATE.md, ROADMAP.md, decisions, predictions, calibration, "what do we know about", "what did I decide", "have I done this before", "remember this", "log this", "add to the roadmap", or asks to start or end a working session; and before answering any question about the user's own past work, projects or decisions.
---

# kea: a second brain that checks its facts

kea keeps a person's facts, decisions, predictions and plans in a folder of plain Markdown, and a local SQLite index
over their past AI sessions, chat exports, git history and notes. You reach it through the `kea` command (every
command takes `--json`) or the kea MCP server (tools such as `brain_status`, `search`, `log_entry`). The CLI finds the
brain from `--brain`, then `$KEA_BRAIN`, then the current folder and its parents, then the user's default; the MCP
server tries the default before the folders, and its initialize message names the brain in use.

If `kea` is not installed: `uv tool install --python 3.15 git+https://github.com/jpsanders/kea` (uv fetches 3.15).
If there is no brain yet, ask the user where it should live, then `kea init <folder>`.

## Start of a session

1. `kea status --json`: what is due, what is stale, which health checks fail.
2. Read `ROADMAP.md` (what waits for the user, what comes next), then the newest entries of `kea state --json`.
3. Tell the user in a few lines what is due and what waits for them. Ask before starting anything new.

## Rules

1. **Verified facts only.** A fact is a row in STATE.md with a source and a date. Anything else is an assumption:
   say so, mark it UNVERIFIED, and say how to check it. Never present an estimate as a measurement.
2. **Search before answering about the user's own past.** `kea search "<words>" --json` covers their agent
   sessions, chat exports, commits, documents and the brain itself. A hit is a lead, not a fact: cite its `ref`,
   open it with `kea show <id> --context 2`, and confirm anything load bearing in the source.
3. **Ask instead of assuming** when two readings of a request lead to different work. Questions you cannot settle go
   on the roadmap: `kea roadmap add "<question>" --waiting`.
4. **Record as you go**, not at the end:
   - what became true: `kea log "<what happened>" --source <where to check it>`
   - a number or state worth keeping: `kea fact "<name>" "<value>" --source "<where it was read>"`
   - a choice made: `kea decide "<title>" --decision "<what>" --why "<reasoning>" --revisit +90d`
   - an expectation: `kea predict "<specific, dated claim>" --by <date> --confidence <0-100>`
   - an idea for later, in the user's words: `kea idea "<words>"`
5. **Close loops.** When a decision's revisit date comes: `kea revisit D-0003 --outcome "..." --surprise 0-10
   --lesson "..."`. When a prediction's date comes: `kea score P-0007 true|false|partial|void --note "<evidence>"`.
6. **Never put secrets in the brain.** No keys, tokens or passwords in any file or command.
7. **The brain's own AGENTS.md wins** when it says something more specific than this skill.

## End of a session

Before the context fills or the topic changes:

1. Close finished roadmap items with their proof: `kea roadmap` shows the numbers, then
   `kea roadmap done <n> --proof "<how you know>"`.
2. Add new open items. Every question for the user goes under waiting.
3. One `kea log` entry: what changed and where the files are.
4. `kea check` and fix what it reports.

## Big plans

Committing more than a month of work, a new product or price, anything expensive to reverse: use the council. See the
`kea-council` skill. To add a thinker's lens to the brain, see the `kea-frameworks` skill.

## Command reference

`kea guide` prints the whole manual. The commands you will use most:

| Command | What it does |
| --- | --- |
| `kea status` | The whole brain and its health checks |
| `kea state` | Facts and the newest log entries |
| `kea check` | Lint: unsourced facts, stale state, overdue items; exit 1 on errors |
| `kea search "q"` / `kea show <id>` | Search the evidence; read one hit with its provenance |
| `kea ingest [source] [path]` | Index claude-code, codex, opencode, chatgpt, claude, git or docs (local only) |
| `kea effort` | The user's own prompts per project: where attention went |
| `kea decisions --due` / `kea predictions --due` | What needs revisiting or scoring |
| `kea calibration` | Brier score and calibration buckets of the scored predictions |
| `kea roadmap` | Open work, numbered |
| `kea digest [transcript]` | A Claude Code session as readable files |
