"""
Keep secrets out of the evidence index, the ledger and council packets.

Two layers. Paths that name a credential store (``.ssh``, ``.env.local``, ``auth.json``, ``id_ed25519``, shell
histories and the like) are never read at all. Everything that is read passes through :func:`redact` first, which
replaces anything shaped like a live credential with a marker, so a token pasted into a chat during debugging never
reaches the index, and never leaves the machine in a packet.

Two strengths. The precise set matches only unmistakable shapes: a provider's key prefix, a private key block, a
password inside a URL, an ``UPPER_CASE_API_KEY=`` assignment. It is what kea applies to what people write into the
ledger, so ordinary words are never touched. The full set adds ``name: value`` pairs whose name is a credential word
(``password``, ``secret``, ``token``, ``api_key``...), matched as whole words so ``Author`` or ``Tokenizer`` stay as
they are; it is what the index and the packets get. Every pattern runs in linear time, whatever the input.
"""

import base64
import binascii
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

# Unmistakable shapes. A pattern with a group named ``v`` replaces only that group.
SHAPES = (
    re.compile(
        r"-----BEGIN [A-Z ]*PRIVATE KEY(?: BLOCK)?-----[\s\S]*?(?:-----END [A-Z ]*PRIVATE KEY(?: BLOCK)?-----|\Z)"
    ),
    re.compile(r"\b(?:AKIA|ASIA)[0-9A-Z]{16}\b"),
    re.compile(r"\bgh[pousr]_[A-Za-z0-9]{36,}\b"),
    re.compile(r"\bgithub_pat_[A-Za-z0-9_]{22,}\b"),
    re.compile(r"\bgl(?:pat|rt|ptt|dt)-[A-Za-z0-9_\-]{20,}"),
    re.compile(r"\bsk-ant-[A-Za-z0-9_\-]{20,}"),
    re.compile(r"\bsk-[A-Za-z0-9_\-]{20,}"),
    re.compile(r"\b(?:sk|rk|pk)_(?:live|test)_[A-Za-z0-9]{16,}\b"),
    re.compile(r"\bwhsec_[A-Za-z0-9+/=]{20,}"),
    re.compile(r"\bxox[abposr]-[A-Za-z0-9\-]{10,}"),
    re.compile(r"\bxapp-[A-Za-z0-9\-]{20,}"),
    re.compile(r"\bAIza[0-9A-Za-z_\-]{35}\b"),
    re.compile(r"\bya29\.[A-Za-z0-9_\-]{20,}"),
    re.compile(r"\bGOCSPX-[A-Za-z0-9_\-]{20,}"),
    re.compile(r"(?<![\w/])1//0[A-Za-z0-9_\-]{20,}"),
    re.compile(r"\b(?:nvapi|xai|csk|pplx)-[A-Za-z0-9_\-]{20,}"),
    re.compile(r"\b(?:hf|gsk|r8|phx)_[A-Za-z0-9]{20,}\b"),
    re.compile(r"\bdop_v1_[a-f0-9]{40,}\b"),
    re.compile(r"\bshp(?:at|ca|pa|ss)_[a-fA-F0-9]{32}\b"),
    re.compile(r"\bATATT[A-Za-z0-9_\-=]{20,}"),
    re.compile(r"\bSG\.[A-Za-z0-9_\-]{16,}\.[A-Za-z0-9_\-]{16,}"),
    re.compile(r"\bnpm_[A-Za-z0-9]{36}\b"),
    re.compile(r"\bpypi-[A-Za-z0-9_\-]{50,}"),
    re.compile(r"\beyJ[A-Za-z0-9_\-]{10,}\.eyJ[A-Za-z0-9_\-]{10,}\.[A-Za-z0-9_\-]{10,}"),
    re.compile(r"hooks\.slack\.com/services/[A-Za-z0-9/]{20,}"),
    re.compile(r"discord(?:app)?\.com/api/webhooks/\d+/[A-Za-z0-9_\-]{20,}"),
    re.compile(r'"type"\s*:\s*"service_account"'),
    re.compile(r"(?<=://)[^/\s:@]{0,64}:(?P<v>[^/\s@]{1,128})(?=@)"),
    re.compile(r"(?<!\S)(?:-u|--user)\s+[^\s:]{1,64}:(?P<v>\S{1,128})"),
    re.compile(
        r"(?<![A-Za-z0-9_])(?:[A-Z0-9]+_)*(?:API_?KEY|SECRET(?:_ACCESS)?(?:_KEY)?|PASSWORD|PASSWD|PASSPHRASE|"
        r"(?:AUTH_|ACCESS_|REFRESH_|API_|BOT_)?TOKEN|PRIVATE_KEY|CLIENT_SECRET)(?:_[A-Z0-9]+)*"
        r"[\"']?\s*[:=]\s*[\"']?(?P<v>[^\s\"',;]{8,})"
    ),
)

# Credential words, matched as whole segments of a name: refresh_token and x-api-key match, tokenizer does not.
_WORDS = {"password", "passwd", "passphrase", "secret", "secrets", "token", "credential", "credentials", "apikey"}
_PAIRS = {("api", "key"), ("access", "key"), ("private", "key"), ("secret", "key"), ("client", "secret")}

# Name and value pairs; the name is judged in code. Bearer and Basic values are checked for their shape.
NAMED = re.compile(r"(?<![\w.\-])(?P<name>[A-Za-z_][\w.\-]{0,63})[\"']?\s*[:=]\s*[\"']?(?P<v>[^\s\"',;]{8,})")
BEARER = re.compile(r"(?i)\b(?:bearer|token)\s+(?P<v>[A-Za-z0-9_\-.=~+/]{20,})")
BASIC = re.compile(r"(?i)\bbasic\s+(?P<v>[A-Za-z0-9+/]{8,}={0,2})(?![\w\-])")
GENERIC = (NAMED, BEARER, BASIC)


def _secret_name(name: str) -> bool:
    parts = [p for p in re.split(r"[_.\-]+|(?<=[a-z])(?=[A-Z])", name) if p]
    lower = [p.lower() for p in parts]
    return any(p in _WORDS for p in lower) or any(pair in _PAIRS for pair in zip(lower, lower[1:]))


def _looks_like_token(value: str) -> bool:
    return any(c.isdigit() for c in value) and any(c.isalpha() for c in value)


def _basic_credentials(value: str) -> bool:
    try:
        decoded = base64.b64decode(value + "=" * (-len(value) % 4), validate=True)
    except binascii.Error, ValueError:
        return False
    return b":" in decoded and all(32 <= b < 127 for b in decoded)


def _swap(match: re.Match[str]) -> str:
    if match.re is NAMED and not _secret_name(match.group("name")):
        return match.group(0)
    if "v" not in match.re.groupindex or match.group("v") is None:
        return MARKER
    value = match.group("v")
    if match.re is BEARER and not _looks_like_token(value):
        return match.group(0)
    if match.re is BASIC and not _basic_credentials(value):
        return match.group(0)
    whole, start = match.group(0), match.start("v") - match.start()
    return whole[:start] + MARKER + whole[start + len(value) :]


def redact_count(text: str, generic: bool = True) -> tuple[str, int]:
    """
    Replace every credential shaped substring with ``[REDACTED]`` and count them.

    :param text: Any text
    :param generic: Also replace values of ``name: value`` pairs named like credentials; off for the ledger
    :returns: The text with secrets replaced, and how many were replaced
    """
    if not text:
        return text, 0
    total = 0

    def swap(match: re.Match[str]) -> str:
        nonlocal total
        out = _swap(match)
        total += out != match.group(0)
        return out

    for pattern in SHAPES + (GENERIC if generic else ()):
        text = pattern.sub(swap, text)
    return text, total


def redact(text: str, generic: bool = True) -> str:
    """
    Replace every credential shaped substring with ``[REDACTED]``.

    :param text: Any text
    :param generic: Also replace values of ``name: value`` pairs named like credentials; off for the ledger
    :returns: The same text with secrets replaced
    """
    return redact_count(text, generic)[0]


def has_secret(text: str) -> bool:
    """
    Tell whether text holds anything shaped like a credential.

    :param text: Any text
    :returns: True when :func:`redact` would change it
    """
    return bool(text) and redact(text) != text


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
