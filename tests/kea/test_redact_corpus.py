"""A corpus of secrets kea must hide and of text, code and commands it must leave alone."""

import pytest

from kea.redact import redact

# Made up tokens in the shapes of real services, built in pieces so that no scanner mistakes this file for a leak.
DISCORD = "MTA0NjY5NzQ1NDA3MTQ4NjQwMA" + "." + "GhR2kL" + "." + "Wq8xYz3AbCdEfGhIjKlMnOpQrStUvWx"
MAPBOX = "sk" + ".eyJ1IjoibWFyYSIsImEiOiJja2x4In0" + "." + "Zx8kq2LmN0pQ7wERtYu"
VAULT_LEGACY = "s" + "." + "AbCdEf1234567890GhIjKlMn"
VAULT = "hvs" + "." + "CAESIJ1x2y3z4AbCdEfGhIjKlMnOpQrStUvWxYz0123456789"
DOPPLER = "dp" + ".st.prd." + "AbCdEf1234567890GhIjKlMnOpQrStUv"
TELEGRAM = "1234567890" + ":" + "AAHdqTcvCH1vGWJxfSeofSAs0K5PALDsawQ"

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
    # Dotted and bracketed secrets, which look like code at a glance.
    ("Staging db: password=correct.horse.battery.staple", "correct.horse.battery.staple"),
    ("DISCORD_TOKEN=" + DISCORD, "Wq8xYz3AbCdEfGhIjKlMn"),
    ("ADMIN_PASSWORD=Xk9m[Qp2!Lz7", "Qp2!Lz7"),
    ("password: MyDog.Rex2019", "MyDog.Rex2019"),
    ("MAPBOX_SECRET_TOKEN=" + MAPBOX, "Zx8kq2LmN0pQ7wERtYu"),
    ("password=aB3dE5[fG7hJ9", "fG7hJ9"),
    ("export VAULT_TOKEN=" + VAULT_LEGACY, "AbCdEf1234567890GhIjKlMn"),
    ("vault token " + VAULT, "CAESIJ1x2y3z4"),
    ("DOPPLER_TOKEN=" + DOPPLER, "AbCdEf1234567890"),
    ("the db password=Summer.Holiday2026 and", "Summer.Holiday2026"),
    ('config.json {"password": "Winter.Is.Coming77"}', "Winter.Is.Coming77"),
    ("password=kT9[mQ2#xL8!", "mQ2#xL8!"),
    ("bot " + TELEGRAM, TELEGRAM[11:]),
    # Letters only tokens under a weak word, and short environment names.
    ("auth_token: QWERTYUIOPASDFGHJKLZ", "QWERTYUIOPASDFGHJKLZ"),
    ("github_token = AbCdEfGhIjKlMnOpQrStUvWx", "AbCdEfGhIjKlMnOpQrStUvWx"),
    ("secret: correcthorsebatterystaple", "correcthorsebatterystaple"),
    ("https://x.example/cb?access_token=AbCdEfGhIjKlMnOpQrSt&state=1", "AbCdEfGhIjKlMnOpQrSt"),
    ("SMTP_PASS=SmtpP4ss2026", "SmtpP4ss2026"),
    ("DB_PASS=DbP4ss2026x", "DbP4ss2026x"),
    ("PASSWORD2=Hunter2Secret99", "Hunter2Secret99"),
    ("stripe.api.key=Xk9mQp2Lr8Tz5Wv1", "Xk9mQp2Lr8Tz5Wv1"),
    ("AccountName=x;AccountKey=Zm9vYmFyYmF6cXV4MTIzNDU2Nzg5MA==;EndpointSuffix=core", "Zm9vYmFyYmF6cXV4"),
    # Placeholder and call shapes with a secret inside.
    ("api_key={Kx9vB2mQ8pL4}", "Kx9vB2mQ8pL4"),
    ("secret=%Ab12Cd34Ef56%", "Ab12Cd34Ef56"),
    ("DB_PASSWORD=$ECRETPASS99", "ECRETPASS99"),
    ("password=Passw0rd(2026)", "Passw0rd(2026)"),
    ("DB_PASSWORD=Hunter2(prod)", "Hunter2(prod)"),
    ("password=Hunter(prod)", "Hunter(prod)"),
    ("client_secret: /Zx8kq2LmN0/pQ7wERt", "Zx8kq2LmN0"),
    ("git log; curl -u admin:Hunter2Secret99 https://x", "Hunter2Secret99"),
    # -u judged by the command it belongs to, and $ values only when they are whole variables.
    ("Checked the cluster: docker exec -it es01 curl -u elastic:Hunter2Secret99 https://x", "Hunter2Secret99"),
    ("cd app && git pull && curl -u admin:password123 https://jenkins.local/job/x/build", "password123"),
    ("kubectl exec -it p -- curl -u admin:RabbitPass2026 http://x", "RabbitPass2026"),
    ("after the git merge I ran curl -u admin:hunter2pass", "hunter2pass"),
    ("podman run box -u deploy:CorrectHorse_Battery9", "CorrectHorse_Battery9"),
    ("curl -u admin:$uperS3cret!2026 https://x", "uperS3cret!2026"),
    ("curl -u $USER:Hunter2Secret99 https://x", "Hunter2Secret99"),
    # Dotted passphrases and $words that only look like code.
    ("password=this.is.my.password", "this.is.my.password"),
    ("DB_PASSWORD=this.is.secret", "this.is.secret"),
    ("password=data.staple", "data.staple"),
    ("DB_PASSWORD=$ecretsauce", "$ecretsauce"),
    ("password=$moneybags", "$moneybags"),
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
    # Code that names a secret without holding one.
    "token = self.token_cache",
    "api_key = settings.API_KEY",
    "password = request.form['password']",
    'api_key = os.getenv("OPENAI_API_KEY")',
    'PASSWD = getpass.getpass("IMAP password for %s")',
    "JWT_SECRET=$(openssl rand -hex 32)",
    "NODE_AUTH_TOKEN: ${{ secrets.NPM_TOKEN }}",
    "NODE_AUTH_TOKEN: ${{secrets.NPM_TOKEN}}",
    "ANTHROPIC_API_KEY=${ANTHROPIC_API_KEY:?set it}",
    'let api_key = env::var("API_KEY")?;',
    "const token = localStorage.getItem('token');",
    "password = self.password.encode('utf-8')",
    'token = state.push("inline", "", 0)',
    'token = response.json()["access_token"]',
    'password=os.environ.get("DB_PASSWORD", "")',
    "PGPASSWORD=$(gcloud secrets versions access latest --secret=db) psql",
    'OPENAI_API_KEY = os.environ.get("OPENAI_API_KEY")',
    "token: process.env.GITHUB_TOKEN",
    "apiKey: import.meta.env.VITE_API_KEY",
    "token = make_token(user, salt)",
    "secret = secrets.token_hex(32)",
    "passwd = HTTPPasswordMgr()",
    'print(f"password={password!r}")',
    "token = Token(ttype, tag, nesting)",
    "token = cls(**dct)",
    "token = peaker.peak(lookahead=index)",
    "token.content = mdTrim(columns[i])",
    "token: Optional[str] = None",
    "word_size_token = line[index:].split()[0]",
    "passwd = _raw_input(prompt, stream, input=input,",
    'pattern = re.compile(r"token")',
    "last_pass = DEFAULT_LAST_PASS",
    "--- PASS: TestSomethingLong (0.01s)",
    # Commands whose -u is not a user and a password.
    "pip install --user git+https://github.com/psf/requests.git",
    "az login --service-principal -u http://app",
    "git diff -u HEAD~1:src/app.py",
    "git diff -u main:src/app.py",
    "docker run -u root:wheel app",
    "docker run -u $UID:$GID app",
    "podman run -u postgres:root db",
    "curl -u admin:$PASSWORD https://x",
    # Nested calls, Terraform and attribute chains that end at a name made for code.
    "SECRET_KEY = hashlib.sha256(os.urandom(32)).hexdigest()",
    "password = hashlib.sha256(password.encode()).hexdigest()",
    "secret = base64.b64encode(os.urandom(32)).decode()",
    "password = var.db_password",
    "password = local.db_password",
    "tokenizer.pad_token = tokenizer.eos_token",
    "password = $password",
    # A pair never crosses a blank line.
    "Password:\n\nEverything is fine here",
    "set OPENAI_API_KEY =\n\nGenerated a fresh one",
]


@pytest.mark.parametrize("text, secret", SECRETS)
def test_every_secret_in_the_corpus_is_hidden(text: str, secret: str) -> None:
    assert secret not in redact(text)


@pytest.mark.parametrize("text", KEEP)
def test_ordinary_text_code_and_commands_are_kept(text: str) -> None:
    assert redact(text) == text and redact(text, generic=False) == text


def test_the_ledger_hides_dotted_passphrases_and_keeps_nested_calls() -> None:
    assert "this.is.secret" not in redact("DB_PASSWORD=this.is.secret", generic=False)
    kept = "SECRET_KEY = hashlib.sha256(os.urandom(32)).hexdigest()"
    assert redact(kept, generic=False) == kept


def test_the_ledger_strength_still_hides_unmistakable_shapes() -> None:
    hidden = [text for text, secret in SECRETS if secret not in redact(text, generic=False)]
    assert len(hidden) >= 12


@pytest.mark.parametrize(
    "text",
    [
        "\n" * 64000,
        "  \n" * 30000,
        "a=b" * 30000,
        "a:" * 100000,
        "TOKEN_" * 10000,
        "password=" + "z" * 3000,
        "a-" * 100000,
        "--a" * 70000,
        "token=a(" * 25000,
        "curl -u a:b " * 20000,
        "docker -u a:" * 20000,
        "PASSWORD=http://x?" * 13000,
        "PASSWORD=${{" * 20000,
        ("x" * 300 + "=" + "y" * 20 + " ") * 600,
    ],
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


def test_random_secrets_almost_never_pass_for_code() -> None:
    import random
    import string

    rng = random.Random(2026)
    alphabet = string.ascii_letters + string.digits + "".join(c for c in string.punctuation if c not in "\"',;")
    leaks = 0
    for _ in range(4000):
        secret = "".join(rng.choice(alphabet) for _ in range(rng.choice((10, 12, 16, 24))))
        for line in (f"DB_PASSWORD={secret} done", f"password: {secret}", f"auth_token = {secret}"):
            leaks += secret in redact(line)
    assert leaks <= 2  # about two in a hundred thousand: shapes such as ./AB/cd, a path made of random letters


def test_generated_tokens_with_dots_or_letters_only_are_hidden() -> None:
    import random
    import string

    rng = random.Random(7)
    alnum = string.ascii_letters + string.digits
    for _ in range(2000):
        dotted = "".join(rng.choice(alnum) for _ in range(10)) + "." + "".join(rng.choice(alnum) for _ in range(22))
        letters = "".join(rng.choice(string.ascii_letters) for _ in range(18))
        assert dotted not in redact(f"password={dotted}") and dotted not in redact(f"API_TOKEN={dotted}", False)
        assert letters not in redact(f"auth_token: {letters}")
