"""
Keep secrets out of the evidence index.

Two layers. Paths whose segments name a credential store (``.ssh``, ``.env``, ``auth.json`` and the like) are never
read at all. Everything that is read passes through :func:`redact` first, which replaces anything shaped like a live
credential with a marker, so a token pasted into a chat during debugging never reaches the index.
"""

import re
from collections.abc import Iterable
from pathlib import PurePath

MARKER = "[REDACTED]"

# Exact path segments (case insensitive) that are never ingested. ``kea.toml`` can add more under [ingest] deny.
DENY_SEGMENTS = frozenset(
    {
        ".aws",
        ".azure",
        ".credentials.json",
        ".docker",
        ".env",
        ".gnupg",
        ".kube",
        ".netrc",
        ".npmrc",
        ".password-store",
        ".pgpass",
        ".pypirc",
        ".ssh",
        "auth.json",
        "credentials",
        "credentials.json",
        "id_dsa",
        "id_ecdsa",
        "id_ed25519",
        "id_rsa",
        "keychain",
        "keychains",
        "secrets",
        "secrets.json",
        "secrets.toml",
        "secrets.yaml",
        "service-account.json",
        "token.json",
        "wallet",
    }
)

# Shapes of live credentials. Each pattern matches the secret itself, so the text around it survives.
SECRET_PATTERNS = (
    re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----[\s\S]*?(?:-----END [A-Z ]*PRIVATE KEY-----|\Z)"),
    re.compile(r"\bAKIA[0-9A-Z]{16}\b"),
    re.compile(r"\bASIA[0-9A-Z]{16}\b"),
    re.compile(r"\bgh[pousr]_[A-Za-z0-9]{36,}\b"),
    re.compile(r"\bgithub_pat_[A-Za-z0-9_]{22,}\b"),
    re.compile(r"\bglpat-[A-Za-z0-9_\-]{20,}\b"),
    re.compile(r"\bsk-ant-[A-Za-z0-9_\-]{20,}"),
    re.compile(r"\bsk-(?:proj-|svcacct-|admin-)?[A-Za-z0-9_\-]{20,}"),
    re.compile(r"\b(?:sk|rk|pk)_(?:live|test)_[A-Za-z0-9]{16,}\b"),
    re.compile(r"\bxox[abposr]-[A-Za-z0-9\-]{10,}"),
    re.compile(r"\bAIza[0-9A-Za-z_\-]{35}\b"),
    re.compile(r"\bnvapi-[A-Za-z0-9_\-]{20,}"),
    re.compile(r"\bhf_[A-Za-z0-9]{30,}\b"),
    re.compile(r"\bgsk_[A-Za-z0-9]{30,}\b"),
    re.compile(r"\bphx_[A-Za-z0-9]{20,}\b"),
    re.compile(r"\bnpm_[A-Za-z0-9]{36}\b"),
    re.compile(r"\bpypi-[A-Za-z0-9_\-]{50,}"),
    re.compile(r"\beyJ[A-Za-z0-9_\-]{10,}\.eyJ[A-Za-z0-9_\-]{10,}\.[A-Za-z0-9_\-]{10,}"),
    re.compile(r"(?i)(?<=bearer )[A-Za-z0-9_\-.=~+/]{20,}"),
    re.compile(r'"type"\s*:\s*"service_account"'),
    re.compile(
        r"(?i)(?<![A-Za-z0-9_])(?:api[_-]?key|apikey|secret|password|passwd|access[_-]?token|auth[_-]?token)"
        r"([\"']?\s*[:=]\s*[\"']?)[^\s\"',;]{8,}"
    ),
)


def redact(text: str) -> str:
    """
    Replace every credential shaped substring with ``[REDACTED]``.

    :param text: Any text
    :returns: The same text with secrets replaced
    """
    if not text:
        return text
    for pattern in SECRET_PATTERNS:
        text = pattern.sub(_marker, text)
    return text


def _marker(match: re.Match[str]) -> str:
    # For key = value shapes, keep the key and the separator so the sentence still reads.
    if match.lastindex:
        start = match.start(1) - match.start()
        head = match.group(0)[: start + len(match.group(1))]
        return head + MARKER
    return MARKER


def has_secret(text: str) -> bool:
    """
    Tell whether text holds anything shaped like a credential.

    :param text: Any text
    :returns: True when :func:`redact` would change it
    """
    return any(pattern.search(text) for pattern in SECRET_PATTERNS) if text else False


def is_denied(path: str | PurePath, extra: Iterable[str] = ()) -> bool:
    """
    Tell whether a path must never be read, because one of its segments names a credential store.

    :param path: The path to test
    :param extra: More segments to deny, from ``kea.toml``
    :returns: True when the path is denied
    """
    deny = DENY_SEGMENTS | {e.lower() for e in extra}
    parts = PurePath(path).parts
    return any(part.lower() in deny for part in parts)
