"""
Talk to the models on the council, with nothing but the standard library.

Three wire formats cover almost every provider: OpenAI compatible chat completions (OpenAI, Mistral, Groq, Cerebras,
NVIDIA, OpenRouter, DeepSeek, xAI, Ollama, LM Studio and any server that speaks it), Google's Gemini, and Anthropic's
Messages API. Keys come from environment variables named in the roster and are only ever sent in headers, never in a
URL, so an error message can never echo one. A key is never sent over plain HTTP to a host that is not this machine.
"""

import http.client
import json
import os
import re
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlparse

from kea import __version__
from kea.redact import redact

USER_AGENT = f"kea/{__version__} (+https://github.com/jpsanders/kea)"

# name: (base URL, key variable, wire format)
PRESETS: dict[str, tuple[str, str | None, str]] = {
    "openai": ("https://api.openai.com/v1", "OPENAI_API_KEY", "openai"),
    "anthropic": ("https://api.anthropic.com/v1", "ANTHROPIC_API_KEY", "anthropic"),
    "gemini": ("https://generativelanguage.googleapis.com/v1beta", "GEMINI_API_KEY", "gemini"),
    "mistral": ("https://api.mistral.ai/v1", "MISTRAL_API_KEY", "openai"),
    "groq": ("https://api.groq.com/openai/v1", "GROQ_API_KEY", "openai"),
    "cerebras": ("https://api.cerebras.ai/v1", "CEREBRAS_API_KEY", "openai"),
    "nvidia": ("https://integrate.api.nvidia.com/v1", "NVIDIA_API_KEY", "openai"),
    "openrouter": ("https://openrouter.ai/api/v1", "OPENROUTER_API_KEY", "openai"),
    "deepseek": ("https://api.deepseek.com/v1", "DEEPSEEK_API_KEY", "openai"),
    "xai": ("https://api.x.ai/v1", "XAI_API_KEY", "openai"),
    "ollama": ("http://localhost:11434/v1", None, "openai"),
    "lmstudio": ("http://localhost:1234/v1", None, "openai"),
}
RETRY_CODES = {408, 409, 425, 429, 500, 502, 503, 504}
LOCAL_HOSTS = {"localhost", "127.0.0.1", "::1"}
SEAT_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9-]{0,40}$")


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    """Refuse every redirect, so a key in a header can never follow one to another host."""

    def redirect_request(self, *args: object, **kwargs: object) -> None:
        """
        Turn a redirect into an error.

        :param args: Ignored
        :param kwargs: Ignored
        :returns: None, which makes urllib raise the redirect as an HTTP error
        """
        return None


_OPENER = urllib.request.build_opener(_NoRedirect())


class ProviderError(Exception):
    """A model could not be reached or answered with an error."""


@dataclass(frozen=True)
class Seat:
    """One seat on the council: who it is, what lens it brings, and which model fills it."""

    name: str
    role: str
    lens: str
    provider: str
    model: str
    base_url: str
    key_env: str | None
    wire: str
    max_tokens: int = 8000
    temperature: float = 0.35
    timeout: int = 600
    enabled: bool = True
    note: str = ""
    remote: bool = False
    custom: bool = False

    @classmethod
    def from_config(cls, raw: dict[str, Any]) -> "Seat":
        """
        Build a seat from one ``[[seat]]`` table of the roster.

        :param raw: The table
        :returns: The seat
        :raises ProviderError: When the provider is unknown and no base_url is given, or a field is missing
        """
        provider = str(raw.get("provider", "")).lower()
        preset = PRESETS.get(provider)
        if preset is None and not raw.get("base_url"):
            raise ProviderError(f"seat {raw.get('name')!r}: unknown provider {provider!r} and no base_url")
        base, key_env, wire = preset or (str(raw["base_url"]), None, "openai")
        if raw.get("base_url") and str(raw["base_url"]).rstrip("/") != base:
            key_env = None  # your own server: it needs a key only when key_env names one
        for needed in ("name", "model"):
            if not raw.get(needed):
                raise ProviderError(f"a seat needs a {needed}: {raw}")
        if not SEAT_NAME.match(str(raw["name"])):
            raise ProviderError(f"seat name {raw['name']!r}: use letters, digits and hyphens")
        custom = bool(raw.get("base_url")) and str(raw["base_url"]).rstrip("/") != (preset[0] if preset else "")
        return cls(
            name=str(raw["name"]),
            role=str(raw.get("role") or str(raw["name"]).title()),
            lens=str(raw.get("lens") or "Read the plan cold and say what a competent outsider would say."),
            provider=provider or "openai",
            model=str(raw["model"]),
            base_url=str(raw.get("base_url") or base).rstrip("/"),
            key_env=str(raw["key_env"]) if raw.get("key_env") else key_env,
            wire=str(raw.get("wire") or wire),
            max_tokens=int(raw.get("max_tokens", 8000)),
            temperature=float(raw.get("temperature", 0.35)),
            timeout=int(raw.get("timeout", 600)),
            enabled=bool(raw.get("enabled", True)),
            note=str(raw.get("note", "")),
            remote=bool(raw.get("remote", False)),
            custom=custom,
        )

    @property
    def key(self) -> str | None:
        """
        The API key from the environment, when the seat needs one.

        :returns: The API key from the environment, when the seat needs one
        """
        return os.environ.get(self.key_env) if self.key_env else None

    @property
    def ready(self) -> bool:
        """
        True when the seat has what it needs to run: its key, or no need for one.

        :returns: True when the seat has what it needs to run: its key, or no need for one
        """
        return self.enabled and (self.key_env is None or bool(self.key))

    @property
    def host(self) -> str:
        """
        The host and port the seat talks to.

        :returns: Such as ``api.mistral.ai`` or ``localhost:11434``
        """
        return urlparse(self.base_url).netloc or self.base_url

    @property
    def stays_here(self) -> bool:
        """
        Whether the packet stays on this machine: a local server, not marked remote, and not an Ollama cloud model.

        :returns: True when nothing leaves the machine
        """
        cloud = "cloud" in self.model.rsplit(":", 1)[-1].lower()
        return self.local and not self.remote and not cloud

    @property
    def local(self) -> bool:
        """
        True when the model runs on this machine.

        :returns: True when the model runs on this machine
        """
        return (urlparse(self.base_url).hostname or "") in LOCAL_HOSTS


def _post(url: str, payload: dict[str, Any], headers: dict[str, str], timeout: int) -> dict[str, Any]:
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https"):
        raise ProviderError(f"refused: {parsed.scheme} URLs are not allowed")
    body = json.dumps(payload).encode()
    request = urllib.request.Request(  # nosec B310: the scheme is checked above
        url, data=body, headers={"Content-Type": "application/json", "User-Agent": USER_AGENT, **headers}
    )
    with _OPENER.open(request, timeout=timeout) as response:  # nosec B310: http or https only, no redirects
        data = json.loads(response.read())
    if not isinstance(data, dict):
        raise ProviderError("the provider answered with something that is not a JSON object")
    return data


def _call(seat: Seat, system: str, user: str, max_tokens: int, temperature: float, timeout: int) -> str:
    try:
        return _ask(seat, system, user, max_tokens, temperature, timeout)
    except (AttributeError, KeyError, TypeError, IndexError) as exc:
        raise ProviderError(f"unexpected response shape from {seat.host}: {type(exc).__name__}") from exc


def _ask(seat: Seat, system: str, user: str, max_tokens: int, temperature: float, timeout: int) -> str:
    key = seat.key
    if seat.key_env and not key:
        raise ProviderError(f"{seat.name}: set {seat.key_env} to use this seat")
    if key and urlparse(seat.base_url).scheme != "https" and not seat.local:
        raise ProviderError(f"{seat.name}: refusing to send a key over plain HTTP to {seat.base_url}")
    if seat.wire == "anthropic":
        data = _post(
            f"{seat.base_url}/messages",
            {
                "model": seat.model,
                "max_tokens": max_tokens,
                "temperature": temperature,
                "system": system,
                "messages": [{"role": "user", "content": user}],
            },
            {"x-api-key": key or "", "anthropic-version": "2023-06-01"},
            timeout,
        )
        return "".join(b.get("text", "") for b in data.get("content", []) if isinstance(b, dict)).strip()
    if seat.wire == "gemini":
        data = _post(
            f"{seat.base_url}/models/{seat.model}:generateContent",
            {
                "systemInstruction": {"parts": [{"text": system}]},
                "contents": [{"role": "user", "parts": [{"text": user}]}],
                "generationConfig": {"maxOutputTokens": max_tokens, "temperature": temperature},
            },
            {"x-goog-api-key": key or ""},
            timeout,
        )
        candidates = data.get("candidates") or []
        if not candidates:
            raise ProviderError(f"no candidates: {redact(json.dumps(data))[:300]}")
        parts = (candidates[0].get("content") or {}).get("parts") or []
        return "".join(p.get("text", "") for p in parts if isinstance(p, dict)).strip()
    headers = {"Authorization": f"Bearer {key}"} if key else {}
    data = _post(
        f"{seat.base_url}/chat/completions",
        {
            "model": seat.model,
            "max_tokens": max_tokens,
            "temperature": temperature,
            "stream": False,
            "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
        },
        headers,
        timeout,
    )
    choices = data.get("choices") or []
    if not choices:
        raise ProviderError(f"no choices: {redact(json.dumps(data))[:300]}")
    message = choices[0].get("message") or {}
    return str(message.get("content") or message.get("reasoning_content") or "").strip()


def complete(
    seat: Seat,
    system: str,
    user: str,
    max_tokens: int | None = None,
    temperature: float | None = None,
    retries: int = 3,
    timeout: int | None = None,
) -> str:
    """
    Ask a seat's model one question and return its answer, retrying rate limits and server errors with backoff.

    :param seat: The seat
    :param system: The system prompt
    :param user: The message
    :param max_tokens: Longest answer; the seat's setting when omitted
    :param temperature: Sampling temperature; the seat's setting when omitted
    :param retries: Attempts in all
    :param timeout: Seconds to wait for one answer; the seat's setting when omitted
    :returns: The answer text
    :raises ProviderError: When every attempt failed
    """
    last = "no attempt made"
    for attempt in range(max(1, retries)):
        try:
            out = _call(
                seat,
                system,
                user,
                max_tokens or seat.max_tokens,
                seat.temperature if temperature is None else temperature,
                timeout or seat.timeout,
            )
            if not out:
                raise ProviderError("the model returned an empty answer")
            return out
        except urllib.error.HTTPError as exc:
            try:
                detail = redact(exc.read().decode(errors="replace"))[:300]
            finally:
                exc.close()
            last = f"HTTP {exc.code}: {detail}"
            if exc.code not in RETRY_CODES:
                break
        except ProviderError as exc:
            last = str(exc)
            if "set " in last or "refus" in last:
                break
        except (OSError, ValueError, http.client.HTTPException) as exc:
            last = f"{type(exc).__name__}: {redact(str(exc))}"
        if attempt + 1 < retries:
            time.sleep(min(60, 4 * 2**attempt))
    raise ProviderError(f"{seat.name} ({seat.provider}/{seat.model}): {last}")


def probe(seat: Seat) -> tuple[bool, str]:
    """
    Check that a seat answers, cheaply and quickly.

    :param seat: The seat
    :returns: Whether it answered, and the answer or the error
    """
    try:
        out = complete(seat, "Reply with exactly: OK", "Reply with exactly: OK", 400, 0, retries=1, timeout=30)
        return True, " ".join(out.split())[:60]
    except ProviderError as exc:
        return False, str(exc)[:200]
