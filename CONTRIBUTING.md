# Contributing

Issues and pull requests are welcome.

## Set up

```sh
git clone https://github.com/jpsanders/kea && cd kea
python3.15 -m venv .venv && . .venv/bin/activate
make init-dev
make demo
```

## Before a pull request

```sh
make format      # black and isort
make checks      # every check CI runs, in the same order
```

`make checks` runs isort, black, editorconfig-checker, darglint, flake8, mypy in strict mode, bandit, the manual and
skills freshness check and pytest with coverage. CI runs the same, then the tests on Linux and macOS, then installs
the built wheel in a clean environment and runs it against the example brain.

## Changing things

- **No dependencies.** kea runs on the standard library alone. A change that needs a package needs a very good reason.
- **The manual** is `docs/MANUAL.md` and **the skills** are `skills/*/SKILL.md`. `make man` copies them into the
  package and builds the man page; `make checks` fails while any copy is stale.
- **Templates** for new brains live in `src/kea/data/templates`, the example brain in `src/kea/data/example`. Keep
  every line at 120 characters or fewer.
- **A new ingest source** is an adapter in `src/kea/ingest.py` that reads, never writes, passes every text through
  `redact`, and comes with a test built on a small fake export.
- **Commits** are signed. Tags carry the bare version, `1.2.0`, never `v1.2.0`.
