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
- **It refuses credential stores.** Paths with a segment such as `.ssh`, `.aws`, `.gnupg`, `.env` or `.env.local`,
  `.netrc`, shell histories, `.pem` or `.key` files, `credentials` or `secrets` are never opened, and a symbolic link
  is judged by where it points and skipped if it leads out of a folder you ingest; add your own patterns in
  `kea.toml` under `[ingest] deny`.
- **It redacts secrets** shaped like API keys, tokens, private keys, passwords in URLs and `NAME_API_KEY=...` lines
  before storing any text and before a council packet is built. In the ledger, where you write your own words, only
  unmistakable key shapes are replaced, so ordinary words are never changed.
- **It keeps the index private**: `.kea/` is readable by you alone, and a forgotten source stays out of later scans.
- **It sends data out only when you run the council.** `kea council` sends the packet (the files inside the brain
  named in `[brief] include`, and the plan, with secrets redacted) to the seats you configured, and asks first, naming
  every host. Over MCP, an agent can run only the seats on this machine unless `kea.toml` sets
  `[council] agents_may_send = true`. API keys travel only in headers, never in URLs, never over plain HTTP to another
  machine, and never after a redirect, because kea follows none.
- **Agents cannot widen what kea reads.** The MCP ingest tool only rescans sources you added from a terminal.
- **The MCP server** speaks over stdio only and opens no port.

## What to keep out of a brain

Keys, tokens and passwords. Free model tiers may keep or train on what they receive: send personal data only to local
seats (Ollama, LM Studio) or providers whose terms you have read.
