# AGENTS.md

Instructions for AI coding agents working on kea itself. People will find them useful too. (To work inside a kea
brain, read that brain's own AGENTS.md, or the skill in `skills/kea/SKILL.md`.)

## Commands

```sh
make init-dev     # install kea and the development tools into the active environment (Python 3.15)
make format       # apply black and isort
make checks       # every check CI runs, in CI's order; run it before you say a change is done
make ruru         # ruru reviews your branch against main: tamper audit, fail first, mutants (in checks)
make man          # rebuild the man page, the packaged manual and the packaged skills
make bench        # start up and command times
make demo         # the example brain in /tmp/kea-demo
make build        # wheel and sdist into build/dist
```

## Layout

```text
src/kea/cli.py          the command line: one cmd_* function per command, lazy imports
src/kea/brain.py        finding a brain, kea.toml, the clock, kea init
src/kea/markdown.py     reading and writing ledger entries without touching other lines
src/kea/ledger.py       facts, log, decisions, predictions, calibration, ideas, roadmap, check, status
src/kea/store.py        the SQLite and FTS5 evidence index
src/kea/ingest.py       the adapters: claude-code, codex, opencode, chatgpt, claude, git, docs, the brain
src/kea/redact.py       the credential path denylist and secret redaction
src/kea/council.py      plans, packets, the parallel council run, the synthesis scaffold
src/kea/providers.py    OpenAI compatible, Gemini and Anthropic clients on urllib
src/kea/mcp.py          the MCP server: tools, resources, prompts
src/kea/digest.py       Claude Code transcripts as readable files
src/kea/skills.py       installing the skills into agent folders
src/kea/data/           templates and example brains, plus generated files: MANUAL.md, kea.1, skills/
docs/MANUAL.md          the one source for kea guide and the man page
skills/                 the agent skills; make man copies them into the package
tests/kea/              pytest, one file per module
```

## Rules the checks enforce

- mypy runs in strict mode, on the tests too: every function has full type hints.
- Docstrings use the Sphinx style and darglint checks them. Private helpers (`_name`) need none.
- Imports are absolute. Lines are at most 120 characters in every file type, Markdown included.
- bandit fails on shell injection and string built SQL; a justified exception carries `# nosec <code>: <reason>`.
- `make checks` fails when `src/kea/data/MANUAL.md`, `kea.1` or the packaged skills are stale.

## Conventions the checks cannot see

- No runtime dependencies. The standard library only; that is a feature.
- Every command that prints data takes `--json` with stable fields. Exit 0 on success, 1 on failure, 2 on bad
  arguments. Commands load their modules lazily so start up stays near 30 ms.
- kea never writes outside the brain, the per user config and the folders a command names. Ingest is read only.
- Nothing leaves the machine except `kea council`, which asks first.
- When a command or option changes, update `docs/MANUAL.md` and the skills in the same commit, then `make man`.
- Add an entry to `CHANGELOG.md` for anything a user would notice.
- Tags carry the bare version, `1.2.0`, never `v1.2.0`. The release workflow checks that the README badge, the
  plugin files, `.mcp.json` and the changelog all name the tag.
- Never commit secrets, a real brain, `.env` files or anything from `build/`.
