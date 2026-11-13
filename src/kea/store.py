"""
The evidence index: one SQLite file per brain, searched with FTS5, every row traceable to where it came from.

Raw exports stay where they are. The index holds the text kea read from them, redacted, with a pointer back (``ref``)
to the exact file and line, message or commit. It lives in ``<brain>/.kea/index.sqlite``, never leaves the machine,
and can be deleted at any time: ``kea ingest`` builds it again.
"""

import re
import sqlite3
from collections.abc import Iterable, Iterator
from contextlib import contextmanager
from dataclasses import astuple, dataclass
from pathlib import Path
from typing import Any

from kea.brain import now

SCHEMA_VERSION = "1"

SCHEMA = """
CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS sources (
    id INTEGER PRIMARY KEY,
    kind TEXT NOT NULL,
    path TEXT NOT NULL,
    label TEXT,
    fingerprint TEXT,
    scanned_at TEXT,
    items INTEGER NOT NULL DEFAULT 0,
    error TEXT,
    UNIQUE (kind, path)
);
CREATE TABLE IF NOT EXISTS items (
    id INTEGER PRIMARY KEY,
    source_id INTEGER NOT NULL REFERENCES sources (id) ON DELETE CASCADE,
    kind TEXT NOT NULL,
    platform TEXT NOT NULL,
    project TEXT,
    thread TEXT,
    title TEXT,
    seq INTEGER NOT NULL DEFAULT 0,
    role TEXT,
    author TEXT,
    mine INTEGER NOT NULL DEFAULT 0,
    ts TEXT,
    ref TEXT NOT NULL,
    body TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS roots (
    kind TEXT NOT NULL,
    path TEXT NOT NULL,
    added_at TEXT NOT NULL,
    PRIMARY KEY (kind, path)
);
CREATE INDEX IF NOT EXISTS ix_items_source ON items (source_id);
CREATE INDEX IF NOT EXISTS ix_items_thread ON items (thread, seq);
CREATE INDEX IF NOT EXISTS ix_items_ts ON items (ts);
"""

FTS = """
CREATE VIRTUAL TABLE IF NOT EXISTS items_fts USING fts5 (
    title, body, content='items', content_rowid='id', tokenize='porter unicode61 remove_diacritics 2'
);
CREATE TRIGGER IF NOT EXISTS items_ai AFTER INSERT ON items BEGIN
    INSERT INTO items_fts (rowid, title, body) VALUES (new.id, coalesce(new.title, ''), new.body);
END;
CREATE TRIGGER IF NOT EXISTS items_ad AFTER DELETE ON items BEGIN
    INSERT INTO items_fts (items_fts, rowid, title, body) VALUES ('delete', old.id, coalesce(old.title, ''), old.body);
END;
CREATE TRIGGER IF NOT EXISTS items_au AFTER UPDATE ON items BEGIN
    INSERT INTO items_fts (items_fts, rowid, title, body) VALUES ('delete', old.id, coalesce(old.title, ''), old.body);
    INSERT INTO items_fts (rowid, title, body) VALUES (new.id, coalesce(new.title, ''), new.body);
END;
"""

ITEM_COLUMNS = "kind, platform, project, thread, title, seq, role, author, mine, ts, ref, body"


@dataclass
class Item:
    """One searchable piece of evidence: a message, a commit, a section of a document."""

    kind: str
    platform: str
    project: str | None
    thread: str | None
    title: str | None
    seq: int
    role: str | None
    author: str | None
    mine: bool
    ts: str | None
    ref: str
    body: str


def fts_query(text: str, any_word: bool = False) -> str:
    """
    Turn free text into a safe FTS5 query: every word quoted, so punctuation never breaks the syntax.

    :param text: What the person typed; ``"a phrase"`` stays a phrase
    :param any_word: Match any word instead of every word
    :returns: The FTS5 query, empty when the text has no words
    """
    terms = []
    for phrase, word in re.findall(r'"([^"]+)"|(\S+)', text):
        chunk = phrase or word
        tokens = re.findall(r"\w+", chunk)
        if not tokens:
            continue
        terms.append('"' + " ".join(tokens) + '"' if phrase else " ".join(f'"{t}"' for t in tokens))
    return (" OR " if any_word else " ").join(terms)


class Store:
    """The index of one brain. Opening it creates it."""

    def __init__(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        self.path = path
        self.db = sqlite3.connect(path, timeout=30, isolation_level=None)
        self.db.row_factory = sqlite3.Row
        self.db.execute("PRAGMA journal_mode = WAL")
        self.db.execute("PRAGMA synchronous = NORMAL")
        self.db.execute("PRAGMA foreign_keys = ON")
        self.db.execute("PRAGMA temp_store = MEMORY")
        self.db.executescript(SCHEMA)
        try:
            self.db.executescript(FTS)
            self.fts = True
        except sqlite3.OperationalError:
            self.fts = False  # a SQLite built without FTS5: search falls back to LIKE
        self.db.execute("INSERT OR IGNORE INTO meta (key, value) VALUES ('schema', ?)", (SCHEMA_VERSION,))

    def close(self) -> None:
        """Close the database."""
        self.db.close()

    def __enter__(self) -> "Store":
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    @contextmanager
    def transaction(self) -> Iterator[None]:
        """
        Run a block in one transaction, so an interrupted ingest leaves the index as it was.

        :yields: Nothing
        :raises BaseException: Whatever the block raised, after the rollback
        """
        self.db.execute("BEGIN IMMEDIATE")
        try:
            yield
        except BaseException:
            self.db.execute("ROLLBACK")
            raise
        self.db.execute("COMMIT")

    def source(self, kind: str, path: str, label: str | None = None) -> tuple[int, str | None]:
        """
        Register a source, or find it.

        :param kind: The adapter, such as ``claude-code``
        :param path: The file, folder or URL it reads
        :param label: A human name, such as the project folder
        :returns: Its id and the fingerprint stored at the last scan
        """
        self.db.execute(
            "INSERT INTO sources (kind, path, label) VALUES (?, ?, ?) "
            "ON CONFLICT (kind, path) DO UPDATE SET label = coalesce(excluded.label, sources.label)",
            (kind, path, label),
        )
        row = self.db.execute(
            "SELECT id, fingerprint FROM sources WHERE kind = ? AND path = ?", (kind, path)
        ).fetchone()
        return int(row["id"]), row["fingerprint"]

    def replace(self, source_id: int, items: Iterable[Item], fingerprint: str | None) -> int:
        """
        Swap a source's items for a fresh set, and record the scan.

        :param source_id: The source
        :param items: Its items, as read now
        :param fingerprint: What identifies this version of the source
        :returns: How many items it now holds
        """
        self.db.execute("DELETE FROM items WHERE source_id = ?", (source_id,))
        rows = [(source_id, *astuple(item)) for item in items]
        sql = f"INSERT INTO items (source_id, {ITEM_COLUMNS}) VALUES ({', '.join('?' * 13)})"  # nosec B608: constants
        self.db.executemany(sql, rows)
        self.db.execute(
            "UPDATE sources SET fingerprint = ?, scanned_at = ?, items = ?, error = NULL WHERE id = ?",
            (fingerprint, now().isoformat(timespec="seconds"), len(rows), source_id),
        )
        return len(rows)

    def add_root(self, kind: str, path: str) -> None:
        """
        Remember a folder or export that was ingested, so a bare ``kea ingest`` scans it again.

        :param kind: The adapter
        :param path: What it read
        """
        self.db.execute(
            "INSERT OR IGNORE INTO roots (kind, path, added_at) VALUES (?, ?, ?)",
            (kind, path, now().isoformat(timespec="seconds")),
        )

    def roots(self) -> list[tuple[str, str]]:
        """
        List the remembered roots.

        :returns: Pairs of adapter and path
        """
        return [(r["kind"], r["path"]) for r in self.db.execute("SELECT kind, path FROM roots ORDER BY kind, path")]

    def fail(self, source_id: int, error: str) -> None:
        """
        Record that a source could not be read.

        :param source_id: The source
        :param error: Why
        """
        self.db.execute(
            "UPDATE sources SET error = ?, scanned_at = ? WHERE id = ?",
            (error[:500], now().isoformat(timespec="seconds"), source_id),
        )

    def drop_missing(self, kind: str, keep: set[str], under: str) -> int:
        """
        Forget the sources of one kind under a folder that no longer exist there.

        :param kind: The adapter
        :param keep: The paths seen in this scan
        :param under: The folder that was scanned
        :returns: How many sources were dropped
        """
        rows = self.db.execute("SELECT id, path FROM sources WHERE kind = ?", (kind,)).fetchall()
        prefix = under.rstrip("/") + "/"
        gone = [r["id"] for r in rows if r["path"].startswith(prefix) and r["path"] not in keep]
        self.db.executemany("DELETE FROM sources WHERE id = ?", [(i,) for i in gone])
        return len(gone)

    def forget(self, ident: int | None = None, kind: str | None = None) -> int:
        """
        Delete sources and everything read from them.

        :param ident: One source's id
        :param kind: Every source of one kind
        :returns: How many sources were deleted
        """
        if ident is not None:
            cur = self.db.execute("DELETE FROM sources WHERE id = ?", (ident,))
        elif kind:
            self.db.execute("DELETE FROM roots WHERE kind = ?", (kind,))
            cur = self.db.execute("DELETE FROM sources WHERE kind = ?", (kind,))
        else:
            return 0
        return cur.rowcount

    def search(
        self,
        query: str,
        kind: str | None = None,
        platform: str | None = None,
        mine: bool = False,
        since: str | None = None,
        limit: int = 10,
        any_word: bool = False,
    ) -> list[dict[str, Any]]:
        """
        Search every item, best match first, each with a snippet and its provenance.

        :param query: Free text; quote a phrase to keep it together
        :param kind: ``message``, ``commit``, ``doc`` or ``note``
        :param platform: Such as ``claude-code``, ``chatgpt``, ``git`` or ``brain``
        :param mine: Only what you wrote: your prompts and your commits
        :param since: Only items dated on or after this ISO date
        :param limit: How many to return
        :param any_word: Match any word instead of every word
        :returns: Hits
        """
        where, params = [], []
        for column, value in (("i.kind", kind), ("i.platform", platform)):
            if value:
                where.append(f"{column} = ?")
                params.append(value)
        if mine:
            where.append("i.mine = 1")
        if since:
            where.append("i.ts >= ?")
            params.append(since)
        extra = (" AND " + " AND ".join(where)) if where else ""
        cols = "i.id, i.kind, i.platform, i.project, i.thread, i.title, i.role, i.author, i.ts, i.ref"
        if self.fts:
            match = fts_query(query, any_word)
            if not match:
                return []
            sql = (
                f"SELECT {cols}, snippet(items_fts, 1, '[', ']', '...', 14) AS snippet "  # nosec B608: fixed columns
                f"FROM items_fts JOIN items i ON i.id = items_fts.rowid WHERE items_fts MATCH ?{extra} "
                "ORDER BY bm25(items_fts, 3.0, 1.0) LIMIT ?"
            )
            rows = self.db.execute(sql, [match, *params, limit]).fetchall()
        else:
            words = re.findall(r"\w+", query)
            if not words:
                return []
            like = " AND ".join("i.body LIKE ?" for _ in words)
            sql = (
                f"SELECT {cols}, substr(i.body, 1, 160) AS snippet FROM items i "  # nosec B608: fixed columns
                f"WHERE {like}{extra} ORDER BY i.ts DESC LIMIT ?"
            )
            rows = self.db.execute(sql, [*(f"%{w}%" for w in words), *params, limit]).fetchall()
        return [dict(r) for r in rows]

    def get(self, ident: int) -> dict[str, Any] | None:
        """
        Read one item with its source.

        :param ident: The item's id, as search shows it
        :returns: The item, or None
        """
        row = self.db.execute(
            "SELECT i.*, s.kind AS source_kind, s.path AS source_path, s.label AS source_label, "
            "s.scanned_at FROM items i JOIN sources s ON s.id = i.source_id WHERE i.id = ?",
            (ident,),
        ).fetchone()
        return dict(row) if row else None

    def around(self, item: dict[str, Any], n: int) -> list[dict[str, Any]]:
        """
        Read the items next to one in the same thread: the turns before and after a message.

        :param item: An item from :meth:`get`
        :param n: How many on each side
        :returns: The neighbours and the item, in order
        """
        if not n or item.get("thread") is None:
            return [item]
        rows = self.db.execute(
            "SELECT id, role, author, ts, seq, title, body, ref FROM items "
            "WHERE source_id = ? AND thread = ? AND seq BETWEEN ? AND ? ORDER BY seq",
            (item["source_id"], item["thread"], item["seq"] - n, item["seq"] + n),
        ).fetchall()
        return [dict(r) for r in rows]

    def sources(self) -> list[dict[str, Any]]:
        """
        List every source, grouped by kind.

        :returns: The sources
        """
        rows = self.db.execute(
            "SELECT id, kind, path, label, items, scanned_at, error FROM sources ORDER BY kind, path"
        ).fetchall()
        return [dict(r) for r in rows]

    def summary(self) -> dict[str, Any]:
        """
        Count what the index holds.

        :returns: Totals by kind and platform, and the last scan time
        """
        by_platform = {
            r["platform"]: r["n"]
            for r in self.db.execute("SELECT platform, count(*) AS n FROM items GROUP BY platform ORDER BY n DESC")
        }
        total = sum(by_platform.values())
        row = self.db.execute("SELECT count(*) AS n, max(scanned_at) AS last FROM sources").fetchone()
        errors = self.db.execute("SELECT count(*) AS n FROM sources WHERE error IS NOT NULL").fetchone()["n"]
        return {
            "items": total,
            "sources": row["n"],
            "last_scan": row["last"],
            "errors": errors,
            "platforms": by_platform,
            "fts": self.fts,
        }

    def effort(self, since: str | None = None, months: bool = False) -> list[dict[str, Any]]:
        """
        Measure where your attention went: your own prompts to AI agents, per project.

        :param since: Only from this ISO date on
        :param months: Split each project by month
        :returns: Rows of project, month (when asked), prompts, characters, first and last date
        """
        group = "coalesce(project, platform)" + (", substr(ts, 1, 7)" if months else "")
        month = "substr(ts, 1, 7) AS month, " if months else ""
        sql = (
            f"SELECT coalesce(project, platform) AS project, {month}count(*) AS prompts, "  # nosec B608: fixed text
            "sum(length(body)) AS chars, min(ts) AS first, max(ts) AS last FROM items "
            "WHERE kind = 'message' AND role = 'user' AND (? IS NULL OR ts >= ?) "
            f"GROUP BY {group} ORDER BY prompts DESC"
        )
        return [dict(r) for r in self.db.execute(sql, (since, since))]
