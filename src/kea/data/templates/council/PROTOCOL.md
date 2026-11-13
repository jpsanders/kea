# Council protocol

Turn a plan into a decision that survived independent challenge, and record enough to learn from what happens.

## The loop

```text
INGEST      a source, a book, a talk           -> frameworks/<name>.md
STATE       what is true, sourced and dated    -> STATE.md
PROPOSE     apply the frameworks to the state  -> plans/<slug>/00-proposal.md, 01-review-questions.md
BRIEF       one packet, identical for all      -> kea brief <slug>
REVIEW      the Chair first, then every seat   -> kea council <slug>
SYNTHESIZE  the mechanical scaffold, then the Chair's judgment -> kea synth <slug>, 11-synthesis.md
DECIDE      accept, modify or reject each      -> 20-production-plan.md, kea decide, kea predict
EXECUTE     do the work, log what happened     -> 30-execution-log.md, kea log
SCORE       predictions against reality        -> kea score, kea calibration, LEARNING.md
```

STATE, EXECUTE and SCORE are where almost everyone quits, and they are the only steps that make the loop compound.

## Rules

**R1. Identical packet.** Every seat receives byte identical input; `kea brief` builds it once and hashes it. If a
seat gets extra context, its disagreement means nothing.

**R2. No cross contamination.** No seat sees another seat's output: not summaries, not hints, not "the previous
reviewer said".

**R3. The Chair writes first, reads second.** The Chair reviews from the packet alone and saves it, and only then
reads the other reviews to write the synthesis. Two sessions, or an explicit stop between them.

**R4. Facts before opinions.** A plan may not depend on a number that is not in STATE.md with a source and a date.

**R5. Every recommendation carries a confidence and a named uncertainty.** Without a number it is a preference.

**R6. Consensus is a signal, not a verdict.** The seats share training data and blind spots. The synthesis has a
mandatory section on what every seat missed.

**R7. Predictions get written down and scored.** At least three, dated and falsifiable, per production plan.

**R8. One council run per plan revision.** The council costs attention. Do not run it again for comfort.

## When to convene

| Trigger | Council |
| --- | --- |
| Committing more than a month of capacity | Yes |
| A new product, market or pricing model | Yes |
| Anything expensive to reverse | Yes |
| A surprise outcome, surprise 6 or more | Yes |
| Tactical choices, tools, copy, scheduling | No. Decide, log it, move |
| You already know the answer and want validation | No. This is the main failure mode |

## Failure modes

| Failure | How it shows | Fix |
| --- | --- | --- |
| Garbage in | Every seat asks the same clarifying question | Rewrite the proposal before running again |
| Sycophancy | Every seat says the plan is good | Ask a seat directly to argue it fails |
| Shared blind spot | Unanimous, and later wrong | The "what every seat missed" section, scored |
| Anchoring | Reviews echo the proposal's own framing | Give one seat a neutral restatement |
| Volume over signal | Many reviews, no decision changed | Track it; after two such cycles, cut seats |
| Council theatre | Running it because it feels productive | R8 |

## Scoring the seats

At each revisit, in LEARNING.md: did the seat's central claim hold, was its confidence calibrated, did it say
something no other seat said that turned out to matter, and was any of its advice acted on. After three cycles, drop
the seats that only ever agree with the consensus.
