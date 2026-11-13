# Security

## Reporting a vulnerability

Please report security problems privately, through GitHub's
[private vulnerability reporting](https://github.com/jpsanders/kea/security/advisories/new), not in a public issue.
You will get an answer within a week.

## What kea does on your machine

- **It keeps your data local.** The brain is a folder you own. The search index is a SQLite file inside it, in
  `.kea/`, which the brain's `.gitignore` keeps out of git. kea has no telemetry and no account.
- **It reads, never writes, your sources.** Ingest opens session files, exports and databases read only, and git only
  through `git log` and `git for-each-ref`.
- **It refuses credential stores.** Paths with a segment such as `.ssh`, `.aws`, `.gnupg`, `.env`, `.netrc`,
  `credentials` or `secrets` are never opened; add your own in `kea.toml` under `[ingest] deny`.
- **It redacts secrets** shaped like API keys, tokens, private keys and `password = ...` pairs before storing any text.
- **It sends data out only when you run the council.** `kea council` sends the packet (the files in `[brief] include`
  and the plan) to the seats you configured, and asks first; the MCP tool needs `confirm: true`. API keys travel only
  in headers, never in URLs, and never over plain HTTP to another machine.
- **The MCP server** speaks over stdio only and opens no port.

## What to keep out of a brain

Keys, tokens and passwords. Free model tiers may keep or train on what they receive: send personal data only to local
seats (Ollama, LM Studio) or providers whose terms you have read.
