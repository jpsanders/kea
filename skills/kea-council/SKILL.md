---
name: kea-council
description: Run a kea council review of a plan, where independent AI models from different labs each review the same hashed packet without seeing each other, the agent acts as Chair (reviews first from the packet alone, synthesizes last), and the result becomes a production plan with dated predictions. Use when the user wants a plan reviewed, stress tested, red teamed or challenged, asks for a second opinion from several models, or is about to commit more than a month of work, launch a product, change a price or make a decision that is expensive to reverse.
---

# Run a council review with kea

The council turns a plan into a decision that survived independent challenge. Every seat reads one byte identical
packet. No seat sees another's review. You are the Chair: you review first, from the packet alone, and you write the
synthesis last. The full rules are in the brain's `council/PROTOCOL.md`; this skill is the procedure.

## When to convene, and when not

Convene for: more than a month of committed work, a new product, market or price, anything expensive to reverse, or a
surprise outcome. Do not convene for tactical choices, or when the user already knows the answer and wants
validation. One run per plan revision; never run it again for comfort.

## Procedure

1. **Propose.** `kea plan new <slug>` (such as `2026-12-pricing`), then help the user write
   `plans/<slug>/00-proposal.md`. Every number cited must exist in STATE.md or METRICS.md with a source and a date;
   list the unverified assumptions in the open. Write 6 to 10 specific questions in `01-review-questions.md`.
2. **Brief.** `kea brief <slug>` builds `council/briefing/<slug>.md` from the files in `[brief] include`, every
   framework the proposal names, the proposal and the questions, and prints its hash.
3. **Chair review, first and alone.** Read `council/briefing/<slug>.md` and nothing else: no other reviews, no other
   files, no earlier synthesis. Answer every question under `## N.` headings, then Top 3 Survival Risks, What Everyone
   Will Miss and a confidence table, and end with the `COUNCIL-JSON` block the packet describes. Save it as
   `plans/<slug>/reviews/<YYYY-MM-DD>_chair_<your model>.md`. Stop before reading anything else.
4. **Seats.** `kea seats` shows which seats are ready. The packet goes to outside providers, so ask the user first;
   then `kea council <slug> --yes` (or `--dry-run` to show what would be sent). `--resume` retries only the failures.
5. **Mechanical synthesis.** `kea synth <slug>` writes `10-synthesis-scaffold.md`: verdicts, convergence, splits and
   solo insights. Read every review now.
6. **Chair synthesis.** Write `11-synthesis.md`. Mandatory: a section on **what every seat missed**, because the models
   share training data and blind spots. Consensus is a signal, not a verdict. A solo insight is either noise or the
   most valuable line in the review: check each by hand.
7. **Decide.** With the user, write `20-production-plan.md`: accept, modify or reject each recommendation with a
   reason, the kill list, and leading indicators. Then record the decision and at least three predictions:
   `kea decide "<title>" --decision "..." --source plans/<slug>/20-production-plan.md --revisit <date>` and
   `kea predict "<claim>" --by <date> --confidence <n> --plan <slug>` for each.
8. **Execute and score.** Log progress in `30-execution-log.md` and STATE.md. At the revisit date, score the
   predictions (`kea score`), revisit the decision (`kea revisit`), and write the lesson in LEARNING.md, including which
   seat was right.

## Failure modes to name out loud

Every seat asks the same clarifying question: the proposal was too vague. Every seat agrees: suspect sycophancy or a
shared blind spot. Reviews echo the proposal's framing: anchoring. Many words and no decision changed: cut seats.
