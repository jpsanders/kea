"""
Keep secrets out of the evidence index and out of council packets.

Two layers. Paths that name a credential store (``.ssh``, ``.env.local``, ``auth.json``, ``id_ed25519``, shell
histories and the like) are never read at all. Everything that is read passes through :func:`redact` first, which
replaces anything shaped like a live credential with a marker, so a token pasted into a chat during debugging never
reaches the index, and never leaves the machine in a packet.
"""

import fnmatch
import re
from collections.abc import Iterable
from pathlib import PurePath

MARKER = "[REDACTED]"

# Path segments (case insensitive, shell style patterns) that are never ingested. kea.toml can add more under
# [ingest] deny.
DENY_PATTERNS = (
    ".aws",
    ".azure",
    ".config/gcloud",
    ".docker",
    ".gnupg",
    ".kube",
    ".ssh",
    ".password-store",
    ".env",
    ".env.*",
    "*.env",
    ".netrc",
    "_netrc",
    ".npmrc",
    ".pypirc",
    ".pgpass",
    ".git-credentials",
    ".credentials.json",
    ".*_history",
    ".histfile",
    "*.pem",
    "*.key",
    "*.p12",
    "*.pfx",
    "*.jks",
    "*.keystore",
    "*.kdbx",
    "*.ovpn",
    "id_rsa*",
    "id_dsa*",
    "id_ecdsa*",
    "id_ed25519*",
    "auth.json",
    "credentials",
    "credentials.*",
    "*credentials*.json",
    "application_default_credentials.json",
    "service-account*.json",
    "secrets",
    "secrets.*",
    "token.json",
    "keychain",
    "keychains",
    "wallet",
)

_NAMES = r"secret|passw(?:or)?d|pwd|token|api[_-]?key|apikey|access[_-]?key|private[_-]?key|credential|auth"

# Shapes of live credentials. A pattern with a group named ``v`` replaces only that group, so ``API_KEY=`` survives
# and the sentence still reads; the others replace the whole match.
SECRET_PATTERNS = (
    re.compile(
        r"-----BEGIN [A-Z ]*PRIVATE KEY(?: BLOCK)?-----[\s\S]*?(?:-----END [A-Z ]*PRIVATE KEY(?: BLOCK)?-----|\Z)"
    ),
    re.compile(r"\b(?:AKIA|ASIA)[0-9A-Z]{16}\b"),
    re.compile(r"\bgh[pousr]_[A-Za-z0-9]{36,}\b"),
    re.compile(r"\bgithub_pat_[A-Za-z0-9_]{22,}\b"),
    re.compile(r"\bglpat-[A-Za-z0-9_\-]{20,}"),
    re.compile(r"\bsk-ant-[A-Za-z0-9_\-]{20,}"),
    re.compile(r"\bsk-[A-Za-z0-9_\-]{20,}"),
    re.compile(r"\b(?:sk|rk|pk)_(?:live|test)_[A-Za-z0-9]{16,}\b"),
    re.compile(r"\bxox[abposr]-[A-Za-z0-9\-]{10,}"),
    re.compile(r"\bAIza[0-9A-Za-z_\-]{35}\b"),
    re.compile(r"\b(?:nvapi|xai|csk|pplx)-[A-Za-z0-9_\-]{20,}"),
    re.compile(r"\b(?:hf|gsk|r8|phx)_[A-Za-z0-9]{20,}\b"),
    re.compile(r"\bnpm_[A-Za-z0-9]{36}\b"),
    re.compile(r"\bpypi-[A-Za-z0-9_\-]{50,}"),
    re.compile(r"\beyJ[A-Za-z0-9_\-]{10,}\.eyJ[A-Za-z0-9_\-]{10,}\.[A-Za-z0-9_\-]{10,}"),
    re.compile(r'"type"\s*:\s*"service_account"'),
    re.compile(r"(?i)\b(?:bearer|basic|token)\s+(?P<v>[A-Za-z0-9_\-.=~+/]{20,})"),
    re.compile(r"(?<=://)[^/\s:@]{1,64}:(?P<v>[^/\s@]{1,128})(?=@)"),
    re.compile(rf"(?i)[\w.\-]*(?:{_NAMES})[\w.\-]*[\"']?\s*[:=]\s*[\"']?(?P<v>[^\s\"',;]{{8,}})"),
)


def _swap(match: re.Match[str]) -> str:
    if "v" in match.re.groupindex and match.group("v") is not None:
        whole, start = match.group(0), match.start("v") - match.start()
        return whole[:start] + MARKER + whole[start + len(match.group("v")) :]
    return MARKER


def redact_count(text: str) -> tuple[str, int]:
    """
    Replace every credential shaped substring with ``[REDACTED]`` and count them.

    :param text: Any text
    :returns: The text with secrets replaced, and how many were replaced
    """
    if not text:
        return text, 0
    total = 0
    for pattern in SECRET_PATTERNS:
        text, n = pattern.subn(_swap, text)
        total += n
    return text, total


def redact(text: str) -> str:
    """
    Replace every credential shaped substring with ``[REDACTED]``.

    :param text: Any text
    :returns: The same text with secrets replaced
    """
    return redact_count(text)[0]


def has_secret(text: str) -> bool:
    """
    Tell whether text holds anything shaped like a credential.

    :param text: Any text
    :returns: True when :func:`redact` would change it
    """
    return bool(text) and redact_count(text)[1] > 0


def is_denied(path: str | PurePath, extra: Iterable[str] = ()) -> bool:
    """
    Tell whether a path must never be read, because a segment of it names a credential store.

    :param path: The path to test; resolve symbolic links first to judge where a link points
    :param extra: More patterns to deny, from ``kea.toml``
    :returns: True when the path is denied
    """
    patterns = [p.lower() for p in (*DENY_PATTERNS, *extra) if p]
    parts = [part.lower() for part in PurePath(path).parts]
    joined = "/".join(parts)
    for pattern in patterns:
        if "/" in pattern:
            if f"/{pattern}/" in f"/{joined}/":
                return True
        elif any(fnmatch.fnmatchcase(part, pattern) for part in parts):
            return True
    return False
