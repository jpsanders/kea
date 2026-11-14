# kea

## NAME

kea - a second brain that checks its facts

## SYNOPSIS

```text
kea [--brain PATH] <command> [options] [--json]
kea init [PATH] [--example]
kea status | check | state | roadmap
kea log TEXT | fact NAME VALUE | decide TITLE | predict CLAIM | score ID OUTCOME
kea search QUERY | show ID | ingest [SOURCE [PATH]] | effort
kea plan new SLUG | brief SLUG | council SLUG | synth SLUG | seats
kea mcp | skill install | digest [TRANSCRIPT] | guide | man
```

## DESCRIPTION

kea keeps a second brain for you and your AI agents in a folder of plain Markdown. It holds facts with their sources
and dates, decisions with the date to look at them again, predictions with a confidence that get scored when their
date comes, a roadmap of open work, and plans that a council of independent AI models reviews before you commit to
them. Next to the folder, kea keeps a local search index over your past AI agent sessions (Claude Code, Codex,
OpenCode), your ChatGPT and Claude exports, your git history and your notes, every hit traceable to its exact source.

It is built around one idea: a fact without a source and a date is an assumption, and an AI agent that cannot tell
the two apart will repeat your guesses back to you as truth. kea makes the difference visible, lints it, and scores
your predictions against what happened, so the brain gets better with use instead of only bigger.

kea is one Python package with no dependencies beyond the standard library. It runs on Python 3.15. Nothing leaves
your machine except a council packet you choose to send.

## QUICK START

```sh
uv tool install --python 3.15 git+https://github.com/jpsanders/kea   # uv fetches Python 3.15 if missing
kea init ~/brain                                        # make a brain; it becomes your default
cd ~/brain && kea status                                # the whole brain at a glance
kea skill install                                       # teach Claude Code, Codex and OpenCode
kea ingest claude-code                                  # make your past agent sessions searchable
kea search "what did we decide about pricing"
```

To explore first, make the example brain, a made up coffee roastery with facts, decisions, scored predictions and a
plan the council reviewed: `kea init /tmp/tidewater --example`.

## THE BRAIN

A brain is a folder with a `kea.toml` in it. `kea init` lays these files down; every one is plain Markdown that people
and agents edit by hand next to the CLI.

| File | What it holds |
| --- | --- |
| ROADMAP.md | Open work: waiting for the owner, next, running by itself, dates. Start every session here |
| STATE.md | What is true now: a facts table (fact, value, source, verified) and a dated log, newest first |
| DECISIONS.md | Decisions D-0001 onwards: the decision, why, status, and when to revisit |
| PREDICTIONS.md | Predictions P-0001 onwards: a claim, a date, a confidence, and the outcome once scored |
| IDEAS.md | Parked ideas I-0001 onwards, in the words they arrived in. Nothing here is active |
| METRICS.md | Measured numbers only; NOT MEASURED rows keep the measurement debt visible |
| LEARNING.md | What reality taught the brain, and which council seats were right |
| SOURCES.md | What to import and why; canonical sources that win any argument |
| VOICE.md | How agents should write for the owner |
| AGENTS.md | How any AI agent works in this brain; CLAUDE.md imports it |
| frameworks/ | Lenses for thinking, each with boundary conditions and a retrodiction score |
| plans/ | One folder per plan: proposal, questions, reviews, synthesis, production plan, execution log |
| council/ | PROTOCOL.md, the rules, and roster.toml, the seats |
| journal/ | Execution logs and the notes a STATE.md entry points to |
| .kea/ | The local search index. Never committed; delete it any time and kea ingest rebuilds it |

Entries are headings kea can read back: `### D-0002 · 2026-11-13 · Title` followed by `- **Field:** value` lines.
Keep that shape when you edit by hand and every other line you write is left exactly as it is.

## THE RULES

These rules are the point of the system. The CLI, the skills and the MCP server all enforce or teach them.

- **Verified facts only.** A fact lives in STATE.md with a source and a date. Anything else is UNVERIFIED and says how
  it would be checked.
- **Never present an estimate as a measurement.** Measured numbers go in METRICS.md with their source.
- **Log decisions** with the reasoning and a revisit date or trigger. Unlogged reasoning does not compound.
- **Write predictions down and score them.** A prediction names a number and a date, and anyone could score it.
- **Import distilled, not raw.** Raw exports stay where they are; name the decision a source changes before you
  import it.
- **Frameworks only with boundary conditions.** A lens cited outside the stage it fits manufactures false confidence.
- **Consensus is a signal, not a verdict.** Models share training data and blind spots; every synthesis says what
  every seat missed.
- **Ask, do not assume.** Questions only the owner can answer go on the roadmap under waiting.

## COMMANDS

Every command that prints data takes `--json` and prints one JSON document with stable fields. `--brain PATH` before
the command picks the brain. Dates accept `2026-12-01`, `today`, `tomorrow`, or an offset such as `+30d`, `+6w`, `+3m`
or, for `--since`, `-30d`.

### Start and look

| Command | What it does |
| --- | --- |
| kea init [PATH] | Make a brain, or put back a brain's missing files; --example, --force (backs up first), --default |
| kea status | The whole brain: freshness, due decisions and predictions, roadmap, plans, seats, evidence, health |
| kea check | Lint: unsourced facts, facts past recheck, malformed entries, overdue items. Exit 1 on errors; --strict |
| kea state | The facts and the newest log entries; --entries N |
| kea roadmap | Open items, numbered, and the dates |

### Write to the ledger

| Command | What it does |
| --- | --- |
| kea log TEXT | A dated entry at the top of STATE.md's log; --title, --source |
| kea fact NAME VALUE --source S | Record or update a fact; --verified DATE (default today) |
| kea decide TITLE --decision D | Log a decision; --why, --revisit DATE or trigger, --source |
| kea decisions | Active decisions; --due for those past their revisit date, --all for closed ones too |
| kea revisit ID --outcome O | Close the loop: --status, --surprise 0 to 10, --lesson, --next DATE |
| kea predict CLAIM --by DATE --confidence N | A prediction from 0 to 100; --plan SLUG, --decision D-0003 |
| kea score ID OUTCOME | Score it: true, false, partial (counts half) or void (left out); --note |
| kea predictions | Every prediction; --due, --open |
| kea calibration | Brier score, skill over a coin, and calibration buckets |
| kea idea TEXT | Park an idea; --why. kea ideas lists them |
| kea roadmap add TEXT | Add to next; --waiting for the owner, --running for jobs |
| kea roadmap done N | Close item N (or a unique piece of its text); --proof |

### Search the evidence

| Command | What it does |
| --- | --- |
| kea ingest SOURCE [PATH] | Index a source locally. Sources: claude-code, codex, opencode, chatgpt, claude, git, docs |
| kea ingest | Scan the brain and every source ingested before again; unchanged files are skipped |
| kea search QUERY | Best matches first; --kind, --platform, --mine, --since DATE, --limit N, --any |
| kea show ID | One hit in full with its source; --context N shows the turns around it |
| kea sources | What the index was read from, with errors |
| kea forget ID | Delete a source and what was read from it; later scans skip it; --kind KIND for a whole kind |
| kea effort | Your own prompts to AI agents per project: where attention went; --months, --since |
| kea digest [TRANSCRIPT] | A Claude Code session as private readable files in the brain's digests/; -o DIR, --tz |

### Plans and the council

| Command | What it does |
| --- | --- |
| kea plan new SLUG | Start a plan from the template |
| kea plan | Every plan, its stage, and its next step |
| kea brief SLUG | Build the packet every seat reads, hashed; --show prints it |
| kea council SLUG | Send the packet to every ready seat in parallel; --seat, --resume, --dry-run, --yes |
| kea synth SLUG | The synthesis scaffold: verdicts, convergence, splits, solo insights |
| kea seats | The seats, their hosts, whether each can run; --probe asks each to answer (--yes for custom hosts) |

### Agents and docs

| Command | What it does |
| --- | --- |
| kea mcp | Serve the brain to agents over the Model Context Protocol on stdio |
| kea skill install | Copy kea's skills where agents read them; --agent claude, agents, codex or opencode; --dry-run |
| kea skill show [NAME] | Print a skill, for agents without a skills folder |
| kea guide | Print this manual as Markdown |
| kea man | Show the man page; --install so plain man kea works, --raw for the roff |

## FINDING THE BRAIN

kea looks, in order: `--brain PATH`, then `$KEA_BRAIN`, then the current folder and each parent, then the default
brain named in `~/.config/kea/config.toml`. The first `kea init` sets the default. A folder without `kea.toml` is
never treated as a brain, so notes kea did not make are never touched.

## EVIDENCE

`kea ingest` reads, never writes, and redacts before it stores. Each source and where kea looks by default:

| Source | What kea reads |
| --- | --- |
| claude-code | `$CLAUDE_CONFIG_DIR/projects` or ~/.claude/projects: your prompts and the replies; no tool calls |
| codex | `$CODEX_HOME/sessions` or ~/.codex/sessions: prompts and replies; injected context is skipped |
| opencode | ~/.local/share/opencode/opencode.db, opened read only: the text of every session |
| chatgpt | Your data export: the zip, its folder, or conversations.json; the kept branch of each chat |
| claude | Your Claude.ai export: conversations, and the documents in projects |
| git | A repository or a folder of them: every commit on every branch, with its files |
| docs | A folder of .md, .txt, .rst and .org files, split into sections by heading |

The brain's own Markdown is indexed too, and refreshed before every search. Your commits and your prompts are marked
as yours, so `kea search --mine` and `kea effort` work: set `[identity]` in kea.toml, or kea reads your global git
config. A second run reads only what changed. Every hit carries a ref: a file and line, an export member and message
id, or a repository and commit.

## THE COUNCIL

Independent models review a plan from one byte identical packet. No seat sees another seat's review. The Chair is
the agent you work with: it reviews first, from the packet alone, and writes the synthesis last.

```text
kea plan new 2026-12-pricing     # write 00-proposal.md and 01-review-questions.md
kea brief 2026-12-pricing        # build and hash the packet
                                 # the Chair reviews from the packet alone
kea council 2026-12-pricing      # every ready seat, in parallel
kea synth 2026-12-pricing        # the scaffold; the Chair writes 11-synthesis.md
kea decide ... && kea predict ... --plan 2026-12-pricing
```

Seats live in `council/roster.toml`. Each names a provider and a model; lineage matters more than raw strength, so the
default roster mixes Google, DeepSeek, OpenAI open weights, Mistral and two local models. A seat whose key variable is
not set, or whose local server is not running, is skipped.

| Provider | Key variable |
| --- | --- |
| gemini | GEMINI_API_KEY |
| anthropic | ANTHROPIC_API_KEY |
| openai | OPENAI_API_KEY |
| mistral | MISTRAL_API_KEY |
| groq | GROQ_API_KEY |
| cerebras | CEREBRAS_API_KEY |
| nvidia | NVIDIA_API_KEY |
| openrouter | OPENROUTER_API_KEY |
| deepseek | DEEPSEEK_API_KEY |
| xai | XAI_API_KEY |
| ollama, lmstudio | none: they run on this machine |

Any other OpenAI compatible server works with `base_url` and `key_env`. Calls to one provider are spaced by the
roster's `[rate_limits]`. Keys travel only in headers, never in a URL, and never over plain HTTP to another machine.

The packet holds the files in `[brief] include` (only files inside the brain) and the plan, with anything shaped
like a key replaced by [REDACTED]. Free tiers may keep or train on what they receive, so `kea council` asks before it
sends anything off the machine, naming every host; `--yes` skips the question and `--dry-run` shows what would go
where. A seat counts as on this machine only when its server is local, it is not marked `remote = true`, and its
model is not an Ollama cloud model. A seat that sends your API key to a custom `base_url` is flagged.

## CALIBRATION

`kea calibration` scores every prediction marked true, false or partial. The Brier score is the mean of the squared
gap between the confidence you stated and what happened: 0 is perfect, 0.25 is what always saying 50% earns. Skill is
how much better than that coin you are. The buckets show, for each band of stated confidence, how often those
predictions came true: if your 80% predictions come true half the time, you are overconfident there.

## AGENTS

kea is made to be used by AI agents as much as by people.

**Skills.** `kea skill install` copies three skills to two places: ~/.claude/skills, which Claude Code and OpenCode
read, and ~/.agents/skills, which Codex and other agents read. Writing an agent's own folder too would make it list
every skill twice, so `--agent codex` and `--agent opencode` write those folders only when asked. kea teaches the
rules and the session routine, kea-council runs a council review, kea-frameworks turns a source into a framework.

**Claude Code plugin.** The repository is a plugin with the skills and the MCP server:

```text
/plugin marketplace add jpsanders/kea
/plugin install kea@kea
```

**MCP server.** `kea mcp` speaks the Model Context Protocol on stdio. Tools: brain_status, state, search, show,
decisions, predictions, roadmap, check, plans, seats, effort, log_entry, record_fact, decide, revisit_decision,
predict, score_prediction, add_idea, roadmap_add, roadmap_done, plan_new, brief, synth, ingest and council_run.
Arguments are checked against each tool's schema. The server uses `KEA_BRAIN`, then your default brain, and only then
a `kea.toml` above the folder the agent runs in, so a project's own files never take over; initialize names the brain
in use. Two limits keep an agent from acting for you: ingest only rescans sources you added in a terminal, and
council_run runs only the seats on this machine unless kea.toml sets `[council] agents_may_send = true`. Resources
expose STATE.md, ROADMAP.md, DECISIONS.md, PREDICTIONS.md and AGENTS.md; prompts start_session, end_session and
chair_review appear as slash commands in clients that show them.

Claude Code, for every project:

```sh
claude mcp add --scope user kea -- kea mcp
```

Codex, in ~/.codex/config.toml:

```toml
[mcp_servers.kea]
command = "kea"
args = ["mcp"]
```

Cursor (~/.cursor/mcp.json) and Gemini CLI (~/.gemini/settings.json) take this JSON. Claude Desktop takes it too, in
claude_desktop_config.json, but starts servers without your shell's PATH: put the full path from `which kea` as the
command there.

```json
{"mcpServers": {"kea": {"command": "kea", "args": ["mcp"], "env": {"KEA_BRAIN": "/home/you/brain"}}}}
```

Without kea installed, use `uvx` as the command and `--python 3.15 --from git+https://github.com/jpsanders/kea kea
mcp` as the arguments; the first start downloads it. Set `KEA_BRAIN` where the client starts servers outside your
projects.

## FILES

| Path | What it is |
| --- | --- |
| kea.toml | Marks a brain; settings for the brain, the packet, your identity and ingest |
| .kea/index.sqlite | The local search index, one per brain |
| council/briefing/ | Built packets; ignored by git |
| ~/.config/kea/config.toml | Your default brain |

## ENVIRONMENT

| Variable | Effect |
| --- | --- |
| KEA_BRAIN | The brain to use when --brain is not given |
| KEA_TODAY, KEA_NOW | Fix the date or the time, for reproducible demos and tests |
| XDG_CONFIG_HOME, XDG_DATA_HOME | Where the config and the man page go |
| CLAUDE_CONFIG_DIR, CODEX_HOME | Where Claude Code and Codex keep sessions and skills |
| NO_COLOR | Plain output, no colour |

## DOCKER

```sh
docker build -t kea . && docker run --rm -it --user "$(id -u):$(id -g)" -v ~/brain:/brain kea status
```

`--user` runs kea as you, so it can write your brain and its index.

## EXIT STATUS

0 on success. 1 when a command fails, when kea check finds an error (or a warning, with --strict), or when no seat
returned. 2 for a mistake in the arguments. With --json, a failure also prints {"error": "..."}.

## PRIVACY AND SECURITY

Everything stays on your machine: the brain is your folder, and the index is a SQLite file inside it, readable only
by you, that git ignores. kea never reads paths that name a credential store (.ssh, .aws, .env and .env.local, shell
histories, .pem and .key files, credentials, secrets and more; add your own patterns under [ingest] deny), judges a
symbolic link by where it points, and replaces anything shaped like a key or token with [REDACTED] before it stores
text, before it writes to the ledger, and before a packet leaves. Changing the deny list rereads what it affects;
a forgotten source stays out of later scans. The only network calls are the council seats you configure and run, and
no request follows a redirect. Report security problems privately:
https://github.com/jpsanders/kea/security/advisories/new

## PERFORMANCE

kea starts in milliseconds: Python 3.15's lazy imports load only what a command uses. The index is SQLite with FTS5
and BM25 ranking; ingest runs in one transaction per source and skips anything unchanged. Measured on one Linux
machine, 1.3 GB of Claude Code transcripts index in about five seconds, a second run takes a tenth of a second, and a
search answers in about 50 ms.

## EXAMPLES

```sh
kea fact "Cash on hand" "EUR 18,400" --source "bank export" --verified 2026-11-10
kea decide "Test wholesale first" --decision "Five sample bags before any lease" --revisit +30d
kea predict "Three of five cafes order" --by +14d --confidence 60 --plan second-cafe
kea score P-0007 partial --note "two ordered, see journal/wholesale.md"
kea search "flat white price" --since 2026-01-01 --json
kea ingest git ~/code && kea search "migration" --mine --kind commit
kea council second-cafe --dry-run
kea roadmap add "Decide on the Harbour Street lease" --waiting
```

## SEE ALSO

The project: https://github.com/jpsanders/kea. The brain's own AGENTS.md and council/PROTOCOL.md.

## AUTHOR

James Phillip Sanders, hello@jamesphillipsanders.com. MIT licence.
