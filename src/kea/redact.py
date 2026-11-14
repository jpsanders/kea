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
    re.compile(r"\bhv[sbr]\.[A-Za-z0-9_\-]{24,}"),
    re.compile(r"\bs\.(?=[A-Za-z0-9]{0,23}\d)(?=[A-Za-z0-9]{0,23}[a-z])(?=[A-Za-z0-9]{0,23}[A-Z])[A-Za-z0-9]{24}\b"),
    re.compile(r"\bdp\.(?:pt|st|ct|sa|scim|audit)\.[A-Za-z0-9_\-.]{20,}"),
    re.compile(r"\bsk\.eyJ[A-Za-z0-9_\-]{10,}\.[A-Za-z0-9_\-]{10,}"),
    re.compile(r"\b[MNO](?=[A-Za-z0-9_\-]{0,24}\d)[A-Za-z0-9_\-]{23,25}\.[A-Za-z0-9_\-]{6}\.[A-Za-z0-9_\-]{27,}"),
    re.compile(r"\b\d{8,10}:AA[A-Za-z0-9_\-]{33}\b"),
    re.compile(r"\bpypi-[A-Za-z0-9_\-]{50,}"),
    re.compile(r"\beyJ[A-Za-z0-9_\-]{10,}\.eyJ[A-Za-z0-9_\-]{10,}\.[A-Za-z0-9_\-]{10,}"),
    re.compile(r"hooks\.slack\.com/services/[A-Za-z0-9/]{20,}"),
    re.compile(r"discord(?:app)?\.com/api/webhooks/\d+/[A-Za-z0-9_\-]{20,}"),
    re.compile(r'"type"\s*:\s*"service_account"'),
    re.compile(r"(?<=://)[^/\s:@]{0,64}:(?P<v>[^/\s@]{1,128})(?=@)"),
)

# Credential words, matched as whole segments of a name: refresh_token and x-api-key match, tokenizer does not. Strong
# words mean a secret whatever the value looks like; weak ones are common in prose ("the secret: patience"), so their
# value must also look like a token. A weak word at the end of a compound name (auth_token, jwt_secret) is strong.
_STRONG = {"password", "passwd", "passphrase", "apikey"}
_WEAK = {"secret", "secrets", "token", "credential", "credentials"}
_PAIRS = {
    ("api", "key"), ("access", "key"), ("private", "key"), ("secret", "key"), ("client", "secret"), ("account", "key"),
    ("master", "key"), ("signing", "key"), ("encryption", "key"), ("auth", "key"), ("app", "key"), ("session", "key"),
    ("shared", "key"), ("license", "key"),
}  # fmt: skip
# A name ending in one of these holds something about a secret, not the secret: secret_name, token_url, api_key_id.
_ABOUT = {
    "name", "names", "path", "paths", "file", "files", "dir", "url", "uri", "id", "ids", "type", "count", "len",
    "length", "size", "env", "var", "field", "header", "prefix", "suffix", "format", "expiry", "expires", "ttl",
    "limit", "scope", "scopes", "label", "hint", "endpoint", "host", "version", "kind", "mode", "policy", "rotation",
    "pattern", "patterns", "re", "regex", "multiline", "index", "idx", "pos", "offset", "start", "end", "line", "lines",
    "handler", "manager", "provider", "factory", "parser", "error", "errors", "exception", "callback", "hook",
    "iterator", "iter", "stream", "reader", "writer", "generator", "gen", "number", "num", "mgr",
}  # fmt: skip
# Shell variables that hold no secret although their names look like one.
_SHELL = {"PWD", "OLDPWD"}
_STRING_PREFIXES = {"r", "b", "f", "u", "rb", "br", "fr", "rf"}

# name: value and name=value. A name is judged in code, and the scan never consumes a value it keeps, so a pair inside
# another value (a URL's query, a JDBC string) is still found. In a URL query a value ends at the next &. A name starts
# only where no other name character stands before it, so a long run of name characters is scanned once.
NAMED = re.compile(r"(?<![\w.\-])(?P<name>-{0,2}[A-Za-z_][\w.\-]{0,254})[\"']?\s*[:=]\s*[\"']?(?P<v>[^\s\"',;]{8,512})")
UPPER = re.compile(r"(?<![A-Za-z0-9_.])(?P<name>[A-Z][A-Z0-9_]{0,254})[\"']?\s*[:=]\s*[\"']?(?P<v>[^\s\"',;]{8,512})")
_VALUE_END = frozenset("\"',;")
BEARER = re.compile(r"(?i)\b(?:bearer|token)\s+(?P<v>[A-Za-z0-9_\-.=~+/]{20,})")
BASIC = re.compile(r"(?i)\bbasic\s+(?P<v>[A-Za-z0-9+/]{8,}={0,2})(?![\w\-])")
COOKIE = re.compile(r"(?im)^[ \t]*(?:set-)?cookie:[ \t]*(?P<v>\S[^\r\n]{7,})")
USER_FLAG = re.compile(r"(?<!\S)(?:-u|--user)\s+(?P<user>[^\s:@/]{1,64}):(?P<v>\S{1,128})")
GENERIC = (BEARER, BASIC, COOKIE, USER_FLAG)

# Values that are code, not secrets. Each shape is tight, so a random token or a person's password almost never fits
# one: a placeholder holds no digit, a call or subscript is cut open at its arguments or closed over plain ones, an
# attribute chain starts at a well known object or ends at an UPPER_CASE constant, and no part of the value may look
# like a token.
_SEG = r"[A-Za-z_]\w*"
_SEP = r"(?:\.|::|->)"
_SUB = r"(?:\[[\w.:]*\])?"
_GROUP = rf"(?:\((?:\*{{0,2}}[\w.]+{_SUB}(?:=[\w.]+{_SUB})?)?\)|\[[\w.:]*\])"
_REF = re.compile(
    r"\$[A-Z_]+|\$[a-z_]+|\$\{\{.*|\$\{[A-Za-z_]\w*(?:\}|(?::?[-=?+]|[#%/^,]{1,2})[\w.\-/:@]*\}?)"
    r"|\$\([a-z_./][a-z0-9_.\-/]*\)?|\{\{.*\}\}|<[A-Za-z][A-Za-z_\-. ]*>|%[A-Za-z_]+%"
    r"|%\([A-Za-z_]\w*\)[a-z]|\{[A-Za-z_][A-Za-z_.]*(?:\([\w.]*\))?(?:![rsa])?(?::[\w.<>^=+\-#,%]*)?\}[)\]]?"
)
_OPEN = re.compile(rf"(?P<root>{_SEG})(?P<more>(?:{_SEP}{_SEG}|{_GROUP})*)[(\[](?P<arg>\*{{0,2}}[\w.]*=?)")
_CLOSED = re.compile(rf"(?P<root>{_SEG})(?P<more>(?:{_SEP}{_SEG})*(?:{_GROUP}(?:{_SEP}{_SEG})*)+)[?]?")
_ROOTS = (
    "self|cls|this|os|sys|env|environ|settings|config|conf|cfg|request|req|ctx|context|app|process|args|opts|options"
    "|params|vault|secrets|data|props|state|import|window|globalThis|module|exports|session|creds|credentials|auth"
    "|response|resp|res|user"
)
_CHAIN = re.compile(
    rf"(?:{_ROOTS})(?:{_SEP}{_SEG})+|[A-Za-z_]+(?:{_SEP}[A-Za-z_]+)*{_SEP}[A-Z][A-Z0-9]*(?:_[A-Z0-9]+)+"
)
_TOP = (
    "home|root|run|etc|var|usr|opt|tmp|srv|mnt|media|proc|dev|sys|app|data|workspace|workspaces|Users|Volumes|private"
)
_PATH = re.compile(rf"(?:~|\.{{1,2}}|/(?:{_TOP}))/[\w.\-]+(?:/[\w.\-]*)*")
_URL = re.compile(r"[A-Za-z][A-Za-z0-9+.\-]*://\S*")
_RUNS = re.compile(r"[A-Za-z0-9]+")
_LOWER = "abcdefghijklmnopqrstuvwxyz"
# How people name things in code: snake_case, camelCase and PascalCase words, or an UPPER_CASE constant.
_NAMED_LIKE = re.compile(r"(?:[A-Z]{2,}(?=[A-Z][a-z]{2}))?[A-Za-z_][a-z0-9_]*(?:[A-Z][a-z0-9_]+)*|[A-Z][A-Z0-9_]*")
_SCHEMES = {"http", "https", "ssh", "git", "ftp", "sftp", "file"}
_CONTAINER = re.compile(r"\b(?:docker|podman|nerdctl|kubectl|ctr|buildah|singularity|apptainer)\b")


def _words(name: str) -> list[str]:
    return [p.lower() for p in re.split(r"[_.\-]+|(?<=[a-z])(?=[A-Z])|(?<=[A-Za-z])(?=\d)", name) if p]


def _secret_name(name: str) -> str | None:
    """Tell how strongly a name says secret: "strong", "weak", or None."""
    segments = [s for s in name.split(".") if s]
    if not segments:
        return None
    name = segments[-1]
    own = _words(name)
    while len(own) > 1 and own[-1].isdigit():
        own.pop()  # password2 and API_TOKEN_2 are judged as password and API_TOKEN
    if not own or own[-1] in _ABOUT or name in _SHELL:
        return None
    # A dotted name is named by its end: stripe.api.key and app.jwt.secret are secrets, token.content is not.
    parts = _words(segments[-2])[-1:] + own if len(segments) > 1 and len(own) == 1 else own
    if any(p in _STRONG for p in parts) or any(pair in _PAIRS for pair in zip(parts, parts[1:])):
        return "strong"
    compound = len(parts) > 1
    if name.isupper() and len(name) > 3:
        # Environment names glue words together: PGPASSWORD, MYSQL_PWD, SLACK_BOTTOKEN, and shorten them: DB_PASS.
        if any(p.endswith(("password", "passwd", "secret", "token")) or p == "pwd" for p in parts):
            return "strong"
        if compound and parts[-1] in ("pass", "pw"):
            return "strong"
    if parts[-1] in _WEAK:
        return "strong" if compound else "weak"
    return "weak" if any(p in _WEAK for p in own) else None


def _tokenish(run: str) -> bool:
    """Tell whether a run of letters and digits looks generated rather than named by a person."""
    digit = any(c.isdigit() for c in run)
    lower = any(c.islower() for c in run)
    upper = sum(c.isupper() for c in run)
    if (len(run) >= 8 and digit and lower and upper) or (len(run) >= 16 and digit and (lower or upper > 0)):
        return True
    if len(run) >= 12 and lower and upper >= 3:
        # getElementsByClassName splits into words; wJalrXUtnFEMI splits into scraps of one or two letters.
        words = re.findall(r"[A-Z]+(?![a-z])|[A-Z]?[a-z]+|\d+", run)
        return sum(len(w) == 1 for w in words) >= 2 or len(run) / len(words) < 3
    return False


def _plain_arg(arg: str) -> bool:
    return re.fullmatch(r"\d+(?:\.\d+)?", arg) is not None or all(_NAMED_LIKE.fullmatch(x) for x in arg.split("."))


def _callable(value: str, rest: str) -> bool:
    """Tell whether a value is a call or a subscript: named like code, with plain arguments, closed or cut open."""
    m = _OPEN.fullmatch(value) or _CLOSED.fullmatch(value)
    if m is None:
        return False
    root, more = m["root"], m["more"]
    names = [root, *re.findall(rf"{_SEP}({_SEG})", re.sub(_GROUP, "", more))]
    if re.search(r"\d[A-Za-z]", root) or not all(_NAMED_LIKE.fullmatch(n) for n in names):
        return False  # Passw0rd(2026), wU(WANb88g) and fFaD(B9e.q) are passwords, not calls
    args = [a for inner in re.findall(r"[(\[]([^()\[\]]*)", value) for a in re.findall(r"[\w.]+", inner)]
    if not all(_plain_arg(a) for a in args):
        return False  # the arguments of a call are names or numbers
    if re.match(_SEP, more) is None:
        bare = root.lstrip("_")
        if bare[:1].isupper():
            # A class or a generic: HTTPPasswordMgr(), Optional[str], Token(name, at the start of a longer call.
            if any(c.isdigit() for c in root) or (args and "[" not in value and m.re is _CLOSED):
                return False  # Hunter2(prod) and Hunter(prod)
        elif root[0] != "_" and (len(root) < 3 or (not root.islower() and len(root) - len(root.lstrip(_LOWER)) < 2)):
            return False  # jRw(AYEPKb): a bare call has a lower case name, getItem or to_string
    if m.re is _CLOSED:
        return True
    following = rest[:1]
    if not m["arg"]:
        return following in ("'", '"', "\n", "\r", "")  # os.getenv("X"), or a call that goes on below
    if m["arg"] in _STRING_PREFIXES and following in ("'", '"'):
        return True  # re.compile(r"...")
    line = rest.split("\n", 1)[0].rstrip()
    return following in (",", " ", "'", '"') and (")" in line or "]" in line or line.endswith((",", "(")))


def _is_code(value: str, rest: str) -> bool:
    """Tell whether a value is code, such as a variable, a call or a path, rather than a secret."""
    if any(_tokenish(run) for run in _RUNS.findall(value)):
        return False
    if _REF.fullmatch(value) or _CHAIN.fullmatch(value) or _PATH.fullmatch(value) or _URL.fullmatch(value):
        return True
    return _callable(value, rest)


def _secret_value(value: str, strength: str, rest: str = "") -> bool:
    if _is_code(value, rest):
        return False
    if strength == "weak":
        # Prose keeps short words ("the secret: patience"); a token holds a digit or a symbol, or is long or mixed case.
        mixed = sum(c.isupper() for c in value) >= 2 and any(c.islower() for c in value)
        return (
            any(not c.isalnum() for c in value)
            or any(c.isdigit() for c in value)
            or len(value) >= 16
            or (len(value) >= 12 and mixed)
        )
    return True


def _looks_like_token(value: str) -> bool:
    return any(c.isdigit() for c in value) and any(c.isalpha() for c in value)


def _basic_credentials(value: str) -> bool:
    try:
        decoded = base64.b64decode(value + "=" * (-len(value) % 4), validate=True)
    except binascii.Error, ValueError:
        return False
    return b":" in decoded and all(32 <= b < 127 for b in decoded)


def _user_flag_kept(match: re.Match[str]) -> bool:
    """Tell whether ``-u a:b`` is not a user and password: a uid, a URL, a git revision, a variable or a group."""
    user, value = match.group("user"), match.group("v")
    if value.isdigit() or value == user or value.startswith(("//", "$")) or user.startswith("$"):
        return True
    if "+" in user or user.lower() in _SCHEMES or "~" in user or "^" in user or user == "HEAD":
        return True
    text = match.string
    line = text[text.rfind("\n", 0, match.start()) + 1 : match.start()]
    if re.search(r"\bgit\b", line) and not any(_tokenish(run) for run in _RUNS.findall(value)):
        return True
    return bool(_CONTAINER.search(line)) and re.fullmatch(r"[A-Za-z_][\w\-]{0,31}", value) is not None


def _keep(match: re.Match[str]) -> bool:
    """Tell whether a match of a shape that needs judging is not a secret after all."""
    value = match.group("v")
    if match.re is BEARER:
        return not _looks_like_token(value)
    if match.re is BASIC:
        return not _basic_credentials(value)
    if match.re is USER_FLAG:
        return _user_flag_kept(match)
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
        if strength and len(value) >= 8 and _secret_value(value, strength, text[end : end + 240]):
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
