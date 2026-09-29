from offsecgym.logging_config import redact


def test_recursive_secret_redaction() -> None:
    raw = {"request": [{"Authorization": "Bearer abc", "body": {"api_key": "xyz"}}]}
    assert redact(raw) == {
        "request": [{"Authorization": "[REDACTED]", "body": {"api_key": "[REDACTED]"}}]
    }
