"""
Keep secrets out of the evidence index, the ledger and council packets.

Two layers. Paths that name a credential store (``.ssh``, ``.env.local``, ``auth.json``, ``id_ed25519``, shell
histories and the like) are never read at all. Everything that is read passes through :func:`redact` first, which
replaces anything shaped like a live credential with a marker, so a token pasted into a chat during debugging never
reaches the index, and never leaves the machine in a packet.

Two strengths. The precise set matches only unmistakable shapes: a provider's key prefix, a private key block, a
password inside a URL, an ``UPPER_CASE_API_KEY=`` assignment. It is what kea applies to what people write into the
ledger, so ordinary words are never touched. The full set adds every ``name: value`` pair whose name is a credential
word (``password``, ``secret``, ``token``, ``api_key``...), matched as whole words so ``Author`` or ``Tokenizer`` stay
as they are, and Bearer, Basic, Cookie and ``-u user:pass`` forms; it is what the index and the packets get. Pairs are
found inside other values too, such as a URL's query. Every pattern runs in linear time, whatever the input.
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
)

# Credential words, matched as whole segments of a name: refresh_token and x-api-key match, tokenizer does not. Strong
# words mean a secret whatever the value looks like; weak ones are common in prose ("the secret: patience"), so their
# value must also look like a token, with a digit or a symbol in it.
_STRONG = {"password", "passwd", "passphrase", "apikey"}
_WEAK = {"secret", "secrets", "token", "credential", "credentials"}
_PAIRS = {("api", "key"), ("access", "key"), ("private", "key"), ("secret", "key"), ("client", "secret")}
# A name ending in one of these holds something about a secret, not the secret: secret_name, token_url, api_key_id.
_ABOUT = {
    "name", "names", "path", "paths", "file", "files", "dir", "url", "uri", "id", "ids", "type", "count", "len",
    "length", "size", "env", "var", "field", "header", "prefix", "suffix", "format", "expiry", "expires", "ttl",
    "limit", "scope", "scopes", "label", "hint", "endpoint", "host", "version", "kind", "mode", "policy", "rotation",
}  # fmt: skip
# Shell variables that hold no secret although their names look like one.
_SHELL = {"PWD", "OLDPWD"}

# name: value and name=value. A name is judged in code, and the scan never consumes a value it keeps, so a pair inside
# another value (a URL's query, a JDBC string) is still found. In a URL query a value ends at the next &.
NAMED = re.compile(r"(?<![A-Za-z0-9_.])(?P<name>[A-Za-z_][\w.\-]{0,254})[\"']?\s*[:=]\s*[\"']?(?P<v>[^\s\"',;]{8,512})")
UPPER = re.compile(r"(?<![A-Za-z0-9_.])(?P<name>[A-Z][A-Z0-9_]{0,254})[\"']?\s*[:=]\s*[\"']?(?P<v>[^\s\"',;]{8,512})")
_VALUE_END = frozenset("\"',;")
BEARER = re.compile(r"(?i)\b(?:bearer|token)\s+(?P<v>[A-Za-z0-9_\-.=~+/]{20,})")
BASIC = re.compile(r"(?i)\bbasic\s+(?P<v>[A-Za-z0-9+/]{8,}={0,2})(?![\w\-])")
COOKIE = re.compile(r"(?im)^[ \t]*(?:set-)?cookie:[ \t]*(?P<v>\S[^\r\n]{7,})")
USER_FLAG = re.compile(r"(?<!\S)(?:-u|--user)\s+(?P<user>[^\s:@/]{1,64}):(?P<v>\S{1,128})")
GENERIC = (BEARER, BASIC, COOKIE, USER_FLAG)

# Values that are code, not secrets, when the whole value has one of these shapes.
_CODE = (
    re.compile(
        r"\$\{[^}]*\}|\$\([^)]*\)|\$[A-Z_][A-Z0-9_]*|\{\{.*\}\}|\{[A-Za-z_]\w*\}|<[A-Za-z][A-Za-z_\-. ]*>"
        r"|%[A-Za-z_]\w*%|%\([A-Za-z_]\w*\)[a-z]"
    ),
    re.compile(r"[A-Za-z_][\w.]*\(.*\)"),
    re.compile(r"[A-Za-z_][\w.]*\[.*"),
    re.compile(r"[A-Za-z_]\w*(?:\.[A-Za-z_]\w*)+"),
    re.compile(r"(?:~|\.{1,2})?/[\w.\-]+(?:/[\w.\-]*)+"),
)


def _words(name: str) -> list[str]:
    return [p.lower() for p in re.split(r"[_.\-]+|(?<=[a-z])(?=[A-Z])", name) if p]


def _secret_name(name: str) -> str | None:
    """Tell how strongly a name says secret: "strong", "weak", or None."""
    parts = _words(name)
    if not parts or parts[-1] in _ABOUT or name in _SHELL:
        return None
    if any(p in _STRONG for p in parts) or any(pair in _PAIRS for pair in zip(parts, parts[1:])):
        return "strong"
    if name.isupper():
        # Environment names glue words together: PGPASSWORD, MYSQL_PWD, SLACK_BOTTOKEN.
        if any(p.endswith(("password", "passwd", "secret", "token")) or p == "pwd" for p in parts) and len(name) > 3:
            return "strong"
    if any(p in _WEAK for p in parts):
        return "weak"
    return None


def _secret_value(value: str, strength: str) -> bool:
    if any(pattern.fullmatch(value) for pattern in _CODE):
        return False
    if strength == "weak":
        return any(c.isdigit() for c in value) or any(not c.isalnum() for c in value)
    return True


def _looks_like_token(value: str) -> bool:
    return any(c.isdigit() for c in value) and any(c.isalpha() for c in value)


def _basic_credentials(value: str) -> bool:
    try:
        decoded = base64.b64decode(value + "=" * (-len(value) % 4), validate=True)
    except binascii.Error, ValueError:
        return False
    return b":" in decoded and all(32 <= b < 127 for b in decoded)


def _keep(match: re.Match[str]) -> bool:
    """Tell whether a match of a shape that needs judging is not a secret after all."""
    value = match.group("v")
    if match.re is BEARER:
        return not _looks_like_token(value)
    if match.re is BASIC:
        return not _basic_credentials(value)
    if match.re is USER_FLAG:
        return value.isdigit() or value == match.group("user")
    return False


def _swap(match: re.Match[str]) -> str:
    if "v" not in match.re.groupindex or match.group("v") is None:
        return MARKER
    if _keep(match):
        return match.group(0)
    whole, start = match.group(0), match.start("v") - match.start()
    return whole[:start] + MARKER + whole[start + len(match.group("v")) :]


def _pairs(pattern: re.Pattern[str], text: str) -> tuple[str, int]:
    out: list[str] = []
    pos = count = 0
    while (m := pattern.search(text, pos)) is not None:
        start, end = m.start("v"), m.end("v")
        if m.start("name") > 0 and text[m.start("name") - 1] in "?&":
            amp = text.find("&", start, end)
            end = amp if amp != -1 else end  # a URL query's value ends at the next pair
        value = text[start:end]
        strength = _secret_name(m.group("name"))
        out.append(text[pos:start])
        if strength and len(value) >= 8 and _secret_value(value, strength):
            if end - start == 512:
                while end < len(text) and not text[end].isspace() and text[end] not in _VALUE_END:
                    end += 1  # a value longer than the pattern's bound is still hidden whole
            out.append(MARKER)
            pos = end
            count += 1
        else:
            pos = start
    out.append(text[pos:])
    return "".join(out), count


def redact_count(text: str, generic: bool = True) -> tuple[str, int]:
    """
    Replace every credential shaped substring with ``[REDACTED]`` and count them.

    :param text: Any text
    :param generic: Also judge every ``name: value`` pair and header shape; off for the ledger, which only loses
        unmistakable shapes and ``UPPER_CASE_SECRET=`` assignments
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
    text, n = _pairs(NAMED if generic else UPPER, text)
    return text, total + n


def redact(text: str, generic: bool = True) -> str:
    """
    Replace every credential shaped substring with ``[REDACTED]``.

    :param text: Any text
    :param generic: Also judge every ``name: value`` pair and header shape; off for the ledger
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
