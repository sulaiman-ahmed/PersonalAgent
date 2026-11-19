from agent.tools.redact import redact, secret_values


def test_secret_values_picks_sensitive_names_only():
    env = {
        "GROQ_API_KEY": "gsk_abcdefgh1234",
        "MY_TOKEN": "tok_12345678",
        "PATH": "/usr/bin:/bin",
        "SHORT_SECRET": "abc",
    }

    assert set(secret_values(env)) == {"gsk_abcdefgh1234", "tok_12345678"}


def test_redact_replaces_all_occurrences():
    assert redact("a SECRET1234 b SECRET1234", ["SECRET1234"]) == "a [REDACTED] b [REDACTED]"


def test_redact_prefers_longest_secret_first():
    secrets = secret_values({"A_KEY": "abcdefgh", "B_KEY": "abcdefgh-extra"})

    assert redact("abcdefgh-extra", secrets) == "[REDACTED]"
