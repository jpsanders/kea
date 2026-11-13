# SOURCES: what to import, and why

**Import distilled, not raw.** Raw exports stay where they are. `kea ingest` indexes them on this machine so they can
be searched, and the facts drawn from them go into STATE.md with the source named. Before importing anything, name
the decision it changes. If you cannot, do not import it.

## Canonical sources

Named authorities. A claim that contradicts one of these is wrong.

| Source | Where | Authority over |
| --- | --- | --- |

## Indexed

`kea sources` lists what the search index was read from. Add more with `kea ingest <kind> <path>`.

## Never imported

| What | Why |
| --- | --- |
| Keys, tokens, password stores | Never. kea refuses those paths and redacts secrets it finds in text |
