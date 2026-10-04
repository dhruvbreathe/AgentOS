"""Tests for secret_guard (uses a fake .env; never touches the real one)."""
import asyncio
import json

import pytest

import secret_guard as sg

FAKE_SR = "eyJhbGciOiJIUzI1NiJ9.eyJyb2xlIjoic2VydmljZV9yb2xlIn0.FAKEsignatureFAKEsignature123"
# Assembled at runtime so the source never holds a token-shaped literal
# (GitHub push protection blocks those, even obviously fake ones).
FAKE_BOT = ".".join(["MTIzNDU2Nzg5MDEyMzQ1Njc4", "GabcDE", "fake" * 8 + "12"])
FAKE_HOOK = "https://discord.com/api/webhooks/123456789012345678/" + "A" * 68


@pytest.fixture(autouse=True)
def fake_env(tmp_path, monkeypatch):
    env = tmp_path / ".env"
    env.write_text(
        f"SUPABASE_SERVICE_ROLE_KEY={FAKE_SR}\n"
        f"DISCORD_BOT_TOKEN='{FAKE_BOT}'\n"
        f"MAIN_WEBHOOK_URL={FAKE_HOOK}\n"
        "APP_STORE_CONNECT_KEY_PATH_X=/Users/x/AuthKey_X.p8\n"
        "SUPABASE_URL=https://abc.supabase.co\n"
    )
    monkeypatch.setattr(sg, "ENV_FILE", env)
    monkeypatch.setattr(sg, "EVENT_LOG", tmp_path / "sg.jsonl")
    sg._cache.update(mtime=None, pairs=[], values={})
    for k in list(__import__("os").environ):
        if sg._is_secret(k, __import__("os").environ[k]):
            monkeypatch.delenv(k, raising=False)
    monkeypatch.delenv("AGENTOS_SECRET_GUARD", raising=False)
    yield


def test_inventory_names_only():
    s = sg.secrets()
    assert set(s) == {"SUPABASE_SERVICE_ROLE_KEY", "DISCORD_BOT_TOKEN", "MAIN_WEBHOOK_URL"}


def test_redact_nested_and_json_escaped():
    obj = {"content": f"key={FAKE_SR}", "nested": [{"x": json.dumps({"t": FAKE_BOT})}]}
    out = sg.redact(obj)
    blob = json.dumps(out)
    assert FAKE_SR not in blob and FAKE_BOT not in blob
    assert "[REDACTED:SUPABASE_SERVICE_ROLE_KEY]" in blob
    assert "[REDACTED:DISCORD_BOT_TOKEN]" in blob


def test_redact_unknown_jwt_shape():
    other = "eyJhbGciOiJIUzI1NiJ9.eyJyb2xlIjoiYW5vbiJ9.someOtherProjectSignature99"
    assert other not in sg.redact_str(f"x {other} y")


def test_redact_entries_drops_signed_thinking_with_secret():
    entry = {"uuid": "u1", "parentUuid": None, "message": {"content": [
        {"type": "thinking", "thinking": f"found {FAKE_SR}", "signature": "sig"},
        {"type": "text", "text": "ok"}]}}
    out = sg.redact_entries([entry])[0]
    types = [b["type"] for b in out["message"]["content"]]
    assert types == ["text"] and out["uuid"] == "u1" and out["parentUuid"] is None


@pytest.mark.parametrize("tool,inp", [
    ("Read", {"file_path": "/Users/x/Developers/ClaudeAgentSDK/.env"}),
    ("Read", {"file_path": "/a/.env.local"}),
    ("Read", {"file_path": "/vault/🔐 Vault/AuthKey_X.p8"}),
    ("Bash", {"command": "env"}),
    ("Bash", {"command": "env | grep SUPABASE"}),
    ("Bash", {"command": "printenv SUPABASE_SERVICE_ROLE_KEY"}),
    ("Bash", {"command": "cat .env"}),
    ("Bash", {"command": "grep SUPABASE /Users/x/.env"}),
    ("Bash", {"command": "head -5 ../.env | sort"}),
    ("Bash", {"command": 'echo "$SUPABASE_SERVICE_ROLE_KEY"'}),
    ("Bash", {"command": "echo ${DISCORD_BOT_TOKEN:0:10}"}),
    ("Bash", {"command": "export -p"}),
    ("Bash", {"command": 'curl -v -H "apikey: $K" https://x.supabase.co/rest/v1/t'}),
    ("Bash", {"command": f"export SUPABASE_SERVICE_ROLE_KEY={FAKE_SR}"}),
    ("Bash", {"command": f"curl -X POST {FAKE_HOOK} -d x"}),
    ("Write", {"file_path": "/vault/note.md", "content": f"token {FAKE_BOT}"}),
    ("Bash", {"command": "python3 -c 'import os; print(dict(os.environ))'"}),
])
def test_blocks(tool, inp):
    assert sg.check_tool_call(tool, inp), (tool, inp)


@pytest.mark.parametrize("tool,inp", [
    ("Read", {"file_path": "/a/.env.example"}),
    ("Read", {"file_path": "/a/environment.md"}),
    ("Bash", {"command": "set -a; source .env; set +a; curl -s -H \"apikey: $SUPABASE_SERVICE_ROLE_KEY\" https://x/rest/v1/t | jq ."}),
    ("Bash", {"command": "cut -d= -f1 .env"}),
    ("Bash", {"command": "grep -c '^SUPABASE_ANON_KEY=' .env"}),
    ("Bash", {"command": 'test -n "$SUPABASE_SERVICE_ROLE_KEY" && echo set'}),
    ("Bash", {"command": 'echo "${#SUPABASE_SERVICE_ROLE_KEY}"'}),
    ("Bash", {"command": "echo $PWD; set -e; env FOO=1 python x.py"}),
    ("Bash", {"command": "#!/usr/bin/env bash\nls"}),
    ("Bash", {"command": 'curl -s -H "Authorization: Bearer $T" https://api.x'}),
    ("Bash", {"command": "git status && npm test"}),
])
def test_allows(tool, inp):
    assert sg.check_tool_call(tool, inp) is None, (tool, inp)


def test_hooks_deny_warn_off(monkeypatch):
    pre, post = sg.make_hooks("t")
    call = {"tool_name": "Bash", "tool_input": {"command": "env"}}
    r = asyncio.run(pre(call, "id", None))
    assert r["hookSpecificOutput"]["permissionDecision"] == "deny"
    monkeypatch.setenv("AGENTOS_SECRET_GUARD", "warn")
    assert "systemMessage" in asyncio.run(pre(call, "id", None))
    monkeypatch.setenv("AGENTOS_SECRET_GUARD", "off")
    assert asyncio.run(pre(call, "id", None)) == {}


def test_post_hook_flags_names_not_values():
    _, post = sg.make_hooks("t")
    r = asyncio.run(post({"tool_name": "Bash", "tool_response": {"stdout": FAKE_SR}}, "id", None))
    ctx = r["hookSpecificOutput"]["additionalContext"]
    assert "SUPABASE_SERVICE_ROLE_KEY" in ctx and FAKE_SR not in ctx
    assert FAKE_SR not in sg.EVENT_LOG.read_text()


@pytest.mark.parametrize("cmd", [
    "grep -n DASHBOARD .env | cut -d= -f1",
    "grep -o -E '^[A-Z_]+=' .env.local | grep -i supabase",
    'echo "present: $([ -z "$SUPABASE_SERVICE_ROLE_KEY" ] && echo no || echo yes)"',
])
def test_allows_names_only_and_presence(cmd):
    assert sg.check_tool_call("Bash", {"command": cmd}) is None


def test_pwd_and_paths_not_secrets(monkeypatch):
    monkeypatch.setenv("PWD", "/Users/someone/Developers/ClaudeAgentSDK")
    sg._cache.update(mtime=None, pairs=[], values={})
    assert "PWD" not in sg.secrets()
    assert sg.check_tool_call("Bash", {"command": "cd /Users/someone/Developers/ClaudeAgentSDK && ls"}) is None


def test_echo_with_default_still_blocked():
    assert sg.check_tool_call("Bash", {"command": 'echo "${SUPABASE_SERVICE_ROLE_KEY:-(empty)}"'})
