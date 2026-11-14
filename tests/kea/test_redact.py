import pytest

from kea.redact import MARKER, has_secret, is_denied, redact

SECRETS = [
    "ghp_" + "A1b2C3d4E5" * 4,
    "github_pat_" + "x" * 30,
    "sk-ant-api03-" + "a" * 30,
    "sk-proj-" + "B" * 30,
    "AKIA" + "ABCDEFGHIJKLMNOP",
    "AIza" + "S" * 35,
    "xoxb-" + "1234567890-abcdef",
    "nvapi-" + "q" * 30,
    "glpat-" + "z" * 22,
    "sk_live_" + "9" * 24,
    "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxMjM0NTY3ODkwIn0.dozjgNryP4J3jVmNHl0w5N_XgL0n3I9PlFUP0THsR8U",
]


@pytest.mark.parametrize("secret", SECRETS)
def test_credentials_are_redacted_and_the_text_around_them_survives(secret: str) -> None:
    text = f"before {secret} after"
    assert has_secret(text)
    out = redact(text)
    assert secret not in out and MARKER in out and out.startswith("before ") and out.endswith(" after")


def test_key_value_pairs_keep_the_key() -> None:
    assert redact("api_key = 'hunter2hunter2'") == "api_key = '[REDACTED]'"
    assert redact('"password": "correcthorsebattery"') == '"password": "[REDACTED]"'
    assert redact("Authorization: Bearer abc123def456ghi789jkl0") == "Authorization: Bearer [REDACTED]"
    assert (
        redact("Authorization: Bearer abcdefghijklmnopqrstuvwxyz") == "Authorization: Bearer abcdefghijklmnopqrstuvwxyz"
    )


def test_private_keys_and_service_accounts() -> None:
    key = "-----BEGIN OPENSSH PRIVATE KEY-----\nabc\ndef\n-----END OPENSSH PRIVATE KEY-----"
    assert redact(f"x {key} y") == f"x {MARKER} y"
    assert has_secret('{"type": "service_account"}')


def test_ordinary_text_is_left_alone() -> None:
    text = "The secret to a good flat white is the milk. Price: EUR 4.20."
    assert redact(text) == text and not has_secret(text) and redact("") == ""


def test_denied_paths() -> None:
    assert is_denied("/home/a/.ssh/id_ed25519")
    assert is_denied("C:/Users/a/.AWS/config")
    assert is_denied("/proj/.env")
    assert not is_denied("/proj/environment/notes.md")
    assert is_denied("/proj/private/notes.md", extra=["private"])
