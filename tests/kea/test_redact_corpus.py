"""A corpus of secrets kea must hide and of text, code and commands it must leave alone."""

import pytest

from kea.redact import redact

# Secrets: (text, secret part that must disappear). Both index and packet strength.
SECRETS = [
    ("export MISTRAL_API_KEY=Xk3m9Qp2Lr8Tz5Wv1Ny7Bc4Hd6Fj0Gs", "Xk3m9Qp2Lr8Tz5Wv1Ny7Bc4Hd6Fj0Gs"),
    ("DB_PASSWORD=$uperS3cret!2026", "$uperS3cret!2026"),
    ("DB_PASSWORD=$ecureP@ss2026", "$ecureP@ss2026"),
    ("SMTP_PASSWORD=p&ssw0rdMail2026", "p&ssw0rdMail2026"),
    ("the admin password=Pa(ss)w0rd123", "Pa(ss)w0rd123"),
    ("password=Hunter2(prod)x", "Hunter2(prod)x"),
    ("spring.datasource.password={noop}plaintextpw1", "{noop}plaintextpw1"),
    ("client_secret: /Zx8kq2LmN0pQ7wE+Rt", "/Zx8kq2LmN0pQ7wE+Rt"),
    ("DB_PASSWORD=%Xk3m9Qp2Lr8Tz5", "Xk3m9Qp2Lr8Tz5"),
    ("DB_PASSWORD=~Xk3m9Qp2Lr8Tz5", "Xk3m9Qp2Lr8Tz5"),
    ("DB_PASSWORD=<Xk3m9Qp2Lr8Tz5>", "Xk3m9Qp2Lr8Tz5"),
    ("password: <Xk3m9Qp2Lr8Tz5>", "Xk3m9Qp2Lr8Tz5"),
    ("PGPASSWORD=PgEnvSecret2026 psql -h db", "PgEnvSecret2026"),
    ("MYSQL_PWD=Hunter2Secret99 mysql", "Hunter2Secret99"),
    ("SPRING_SECURITY_OAUTH2_CLIENT_REGISTRATION_KEYCLOAK_CLIENT_SECRET=Hunter2Secret99", "Hunter2Secret99"),
    ("curl https://api.example/v1/now?api_key=Wk3yLiveA1b2C3d4E5&units=metric", "Wk3yLiveA1b2C3d4E5"),
    ("mysql -u root --password=Hunter2Secret99", "Hunter2Secret99"),
    ("jdbc:postgresql://db:5432/app?user=app&password=PgSecret2026x", "PgSecret2026x"),
    ("Cookie: sessionid=abc123def456ghi; csrftoken=zzz", "abc123def456ghi"),
    ("--api-key=abcd1234efgh5678", "abcd1234efgh5678"),
    ("curl -u AKIAIOSFODNN7EXAMPLE:wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY https://s3", "bPxRfiCYEXAMPLEKEY"),
    ("curl -u admin:Zq/8kLm+3xYp0wQ== https://x", "Zq/8kLm+3xYp0wQ=="),
    ("curl -u admin:pw1 https://x", "pw1"),
    ("refresh_token: 1//0gAbCdEfGhIjKlMnOpQrStUv", "1//0gAbCdEfGhIjKlMnOpQrStUv"),
    ("x-api-key: abcdef1234567890", "abcdef1234567890"),
    ("apiKey = 'abcdef12345678'", "abcdef12345678"),
    ("redis://:RedisPassw0rd@cache:6379", "RedisPassw0rd"),
    ("Authorization: Basic dXNlcjpwYXNz", "dXNlcjpwYXNz"),
    ("Bearer eyJhbGciOiJIUzI1NiJ9abc123def456", "eyJhbGciOiJIUzI1NiJ9abc123def456"),
    ("password: correcthorsebattery", "correcthorsebattery"),
    ("SECRET_KEY = 'django-insecure-abcdef123456'", "django-insecure-abcdef123456"),
    ("ghp_" + "a1B2c3D4" * 5, "ghp_"),
    ("sk-proj-" + "B" * 30, "sk-proj-"),
    ("AKIAIOSFODNN7EXAMPLE", "AKIAIOSFODNN7EXAMPLE"),
    ("token: abc123def456ghi789", "abc123def456ghi789"),
]
# Text that must stay exactly as it is, at both strengths.
KEEP = [
    "Food authority: Westminster Council inspected",
    "Author: Jonathan Strange",
    "Tokenizer: sentencepiece_model",
    "Authorised: Christopher Wren approved",
    "pwd: /home/ann/projects/shop",
    "basic authentication-middleware",
    "the basic reasoning is sound",
    "oauth_url: https://example.com/callback",
    "Source: journal/wholesale-samples.md",
    "token = get_token(user)",
    "secret_name = os.environ.get(name)",
    "credentials_path = Path.home() / 'creds'",
    "docker run -u 1000:1000 app",
    "podman exec -u root:root ctr sh",
    "rsync -u deploy@host:/srv/app",
    "--user node:node",
    "The secretary: Maria-Fernandes",
    "tokens: 12000000",
    "TOKEN_COUNT=123456789",
    "The secret: patience, and a hot oven.",
    "PWD=/home/ann/projects/kea",
    "OLDPWD=/home/ann",
    "password = settings.DATABASE_PASSWORD",
    "password=${DB_PASSWORD}",
    "DB_PASSWORD=$DB_PASSWORD_FROM_VAULT",
    "secret: {{ vault_secret }}",
    "api_key = os.environ['API_KEY']",
    "token_url: https://auth.example.com/token",
    "Rotated API_TOKEN for the team on Monday",
    "the token limit is 8000 per minute",
    "password = <your-password-here>",
    "token = %(token)s",
    "password={password}",
    "password = %APPDATA%",
]


@pytest.mark.parametrize("text, secret", SECRETS)
def test_every_secret_in_the_corpus_is_hidden(text: str, secret: str) -> None:
    assert secret not in redact(text)


@pytest.mark.parametrize("text", KEEP)
def test_ordinary_text_code_and_commands_are_kept(text: str) -> None:
    assert redact(text) == text and redact(text, generic=False) == text


def test_the_ledger_strength_still_hides_unmistakable_shapes() -> None:
    hidden = [text for text, secret in SECRETS if secret not in redact(text, generic=False)]
    assert len(hidden) >= 12


@pytest.mark.parametrize(
    "text", ["\n" * 64000, "  \n" * 30000, "a=b" * 30000, "a:" * 100000, "TOKEN_" * 10000, "password=" + "z" * 3000]
)
def test_adversarial_inputs_stay_fast(text: str) -> None:
    import time

    start = time.perf_counter()
    redact(text)
    redact(text, generic=False)
    assert time.perf_counter() - start < 2


def test_a_non_breaking_space_ends_a_value() -> None:
    out = redact("Rotated API_TOKEN=abcdefgh12345 on Monday, then told the team", generic=False)
    assert out == "Rotated API_TOKEN=[REDACTED] on Monday, then told the team"
