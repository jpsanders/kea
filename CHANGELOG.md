# Changelog

Versions follow [semantic versioning](https://semver.org). Tags carry the bare version, such as `1.0.0`.

## 1.0.0 (2026-11-13)

The first release.

- **The ledger**, in plain Markdown that people and agents edit side by side: facts with a source and a verified date
  (`kea fact`), a dated log (`kea log`), decisions with a revisit date or trigger (`kea decide`, `kea revisit`),
  predictions with a date and a confidence (`kea predict`, `kea score`), parked ideas and a roadmap with a section for
  what waits for the owner.
- **Calibration**: `kea calibration` gives the Brier score, skill over a coin, and ten buckets of stated against
  observed confidence.
- **Health**: `kea status` shows the whole brain and six health checks; `kea check` lints unsourced facts, facts past
  their recheck date, malformed entries and overdue decisions and predictions, and exits 1 on errors.
- **The council**: `kea plan new`, `kea brief` (one hashed packet for every seat), `kea council` (every ready seat in
  parallel, with per provider pacing, after asking before anything leaves the machine) and `kea synth` (verdicts,
  convergence, splits and solo insights). Twelve providers preset, any OpenAI compatible server, Ollama and LM Studio
  for local seats.
- **Evidence search**: `kea ingest` indexes Claude Code, Codex and OpenCode sessions, ChatGPT and Claude exports, git
  history and folders of notes into SQLite with FTS5, incrementally, read only, with credential paths refused and
  secrets redacted. `kea search`, `kea show` with provenance and context, `kea effort` for where attention went,
  `kea forget`.
- **Agents**: an MCP server with 25 tools, five resources and three prompts; three skills installed into Claude Code,
  Codex, OpenCode and `~/.agents` by `kea skill install`; a Claude Code plugin; `AGENTS.md` and `CLAUDE.md` in every
  brain.
- **Safety**: nothing shaped like a key or token is stored in the index or sent in a packet (provider keys,
  `NAME_API_KEY=` lines, passwords in URLs, private keys), and unmistakable key shapes never reach the ledger;
  credential paths such as `.ssh`, `.env.local`, shell histories and `.pem` files are refused, symbolic links
  included; the index is private to you; requests follow no redirects; agents on MCP can neither add sources nor send
  a packet off the machine unless `kea.toml` allows it.
  Three independent reviews before release found 56 issues. Two further rounds of verification, by two more agents
  each, found 41 problems in the fixes and 18 fixes that were incomplete. Every one is fixed and pinned by a test.
- `kea digest` turns a Claude Code transcript into private readable files in the brain's `digests/` folder.
- `kea init --example` makes a complete example brain to explore. `kea init` on an existing brain puts back missing
  files and changes nothing else; `--force` keeps a backup of every file it overwrites.
- A manual for people and agents: `kea guide`, `kea man`, `man kea` after `kea man --install`.
- Python 3.15, no dependencies, lazy imports: about 30 ms to start.
