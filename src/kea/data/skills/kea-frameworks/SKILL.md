---
name: kea-frameworks
description: Turn a book, talk, interview, transcript or a thinker's body of work into a kea framework file with a mechanism for each idea, a decision checklist, boundary conditions, conflicts with other frameworks and a retrodiction test against three past decisions with known outcomes. Use when the user wants to add a mental model, a thinker's ideas, a playbook or a lens to their second brain, or asks whether a framework applies to their situation.
---

# Add a framework to a kea brain

A framework is a lens the brain can apply to plans. It is not usable until it has boundary conditions and a
retrodiction score: a framework cited outside its stage, or for the opposite of what its author argued, manufactures
confidence that was never earned. `kea check` warns about any framework without both.

## Procedure

1. **Name the decision it changes.** Before reading anything, ask the user which class of decision this framework
   should improve. If there is none, stop: it is trivia, and the brain imports distilled, not raw.
2. **Read the source.** Work from the actual text or transcript. Summarise in your own words; never paste long
   quotes from copyrighted works.
3. **Copy the template.** `frameworks/_TEMPLATE.md` to `frameworks/<slug>.md`, and fill the front matter.
4. **Fill the boundary conditions first.** What the framework assumes, what happens when each assumption is false,
   the stage the thinker was at when they formed the view, who it does not fit.
5. **Core ideas.** For each: the claim in one sentence, the mechanism (or mark it an assertion), the decision it
   changes, and when it applies in this brain.
6. **Decision checklist.** Questions a plan can PASS, FAIL or WARN, each with the evidence required.
7. **Conflicts.** Name every framework in `frameworks/` this one disagrees with, on what, and which wins when.
8. **Retrodiction test.** Find three past decisions in DECISIONS.md or LEARNING.md with known outcomes (use
   `kea decisions --all` and `kea search`). Record what the framework would have predicted and what happened. Score it
   n of 3 and set `retrodiction_score` in the front matter.
9. **Index it.** Add a row to `frameworks/INDEX.md`. Set `always_apply: true` only for a framework that belongs in
   every council packet.
10. **Check.** `kea check` must show no warning for the new file.

One new framework a month at most. A framework with an empty application log after 90 days gets archived.
