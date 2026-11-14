# AGENTS.md: how to work in this brain

This folder is a kea brain: the facts, decisions, predictions and plans of {{owner}}. Read this file before anything
else, and follow it in every session. The kea command line reads and writes every file here. The kea MCP server
works on the default brain unless `KEA_BRAIN` names another, so check the brain that `brain_status` names first.

## Read order

1. `ROADMAP.md`: what is open, what waits for {{owner}}, and what comes next.
2. `STATE.md`: what is true. Nothing outside it is a fact.
3. Then only what the task needs. Loading every file into every session dilutes it.

`kea status` gives the whole picture in one screen: what is due, what is stale, which checks fail.

## Rules

1. **Verified facts only.** A fact lives in STATE.md with a source and a date. Anything else is an assumption: say so
   out loud, mark it UNVERIFIED, and say how it would be checked.
2. **Never present an estimate as a measurement.** Measured numbers go in METRICS.md with their source.
3. **Ask, do not assume.** When two readings of a request lead to different work, ask {{owner}} first.
4. **Log decisions** with why and when to revisit: `kea decide`.
5. **Write predictions down** with a date and a confidence, and score them when the date comes: `kea predict`,
   `kea score`.
6. **Search before answering** about {{owner}}'s own past work: `kea search`. A search hit is a lead, not a fact:
   cite its ref, and check anything load bearing in the source.
7. **Import distilled, not raw.** Raw exports stay where they are. Name the decision a source changes before you
   import it.
8. **Frameworks only with boundary conditions.** Never cite a framework outside the stage it fits without saying why.
9. **Consensus is a signal, not a verdict.** When every council seat agrees, ask what they were all trained not to see.
10. **Never put secrets in this brain.** No keys, tokens or passwords in any file.

## Commands

```sh
kea status                      # the whole brain, with health checks
kea state                       # facts and the newest log entries
kea search pricing decision
kea log "What happened" --source <where to check it>
kea fact "Cash on hand" "EUR 4,200" --source "bank export"
kea decide "Title" --decision "..." --why "..." --revisit +90d
kea predict "Specific claim" --by +30d --confidence 70
kea roadmap add "A question for {{owner}}" --waiting
kea check                       # lint: unsourced facts, overdue items
```

## Ending a session

Before the context fills or the topic changes:

1. Close finished items in ROADMAP.md with the date and the proof: `kea roadmap done <n> --proof "..."`.
2. Add the new items. Every question for {{owner}} goes under "Waiting for {{owner}}".
3. Write one STATE.md entry saying what changed and where the files are: `kea log`.
4. Run `kea check` and fix what it finds.

## Roles in a council review

- **Author** writes `plans/<slug>/00-proposal.md`. Every fact cited traces to STATE.md.
- **Chair, review pass** reads `council/briefing/<slug>.md` and nothing else, writes an independent review to
  `plans/<slug>/reviews/`, and stops.
- **Chair, synthesis pass** comes only after every seat returned: `kea synth <slug>`, then `11-synthesis.md` and
  `20-production-plan.md`, with the mandatory section on what every seat missed.

The full protocol: `council/PROTOCOL.md`.
