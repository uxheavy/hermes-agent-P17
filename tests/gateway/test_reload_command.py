# Copyright (c) 2026-present Ngo Quoc Huy
# SPDX-License-Identifier: MIT

"""Gateway ``/reload``: contracts for a live profile reload.

- success clears the stored system prompt and evicts the cached agent, so the
  next turn rebuilds from disk; the transcript is untouched
- a broken config.yaml reports an error and leaves the stored prompt and the
  cached agent exactly as they were
- the gateway never wipes variables that live only in the process environment
  (GATEWAY_ALLOW_ALL_USERS regression: reload_env() deletes known Hermes vars
  that are absent from .env, locking launchd-configured users out)
"""

import threading
from types import SimpleNamespace

import pytest

from gateway.config import Platform
from gateway.platforms.base import MessageEvent, MessageType
from gateway.run import GatewayRunner
from gateway.session import SessionSource
from hermes_constants import get_hermes_home
from hermes_state import AsyncSessionDB, SessionDB

SESSION_ID = "reload-session"
SESSION_KEY = "agent:main:telegram:dm:12345"


def _make_runner(db: SessionDB, cached_agent):
    runner = object.__new__(GatewayRunner)
    runner._agent_cache = {SESSION_KEY: (cached_agent, "sig")}
    runner._agent_cache_lock = threading.Lock()
    runner._session_db = AsyncSessionDB(db)
    runner.session_store = SimpleNamespace(
        _generate_session_key=lambda source: SESSION_KEY,
        get_or_create_session=lambda source, **kw: SimpleNamespace(session_id=SESSION_ID),
    )
    return runner


def _event():
    return MessageEvent(
        text="/reload",
        message_type=MessageType.TEXT,
        source=SessionSource(platform=Platform.TELEGRAM, chat_id="12345", chat_type="dm"),
    )


@pytest.fixture
def seeded():
    """A real session with a stored system prompt and two history messages."""
    db = SessionDB(get_hermes_home() / "state.db")
    db.create_session(SESSION_ID, "telegram")
    db.update_system_prompt(SESSION_ID, "OLD PROMPT")
    db.append_message(SESSION_ID, "user", "hello")
    db.append_message(SESSION_ID, "assistant", "hi")
    yield db
    db.close()


def _stored_prompt(db):
    return (db.get_session(SESSION_ID) or {}).get("system_prompt")


@pytest.mark.asyncio
async def test_successful_reload_clears_prompt_and_cached_agent_keeps_history(seeded):
    (get_hermes_home() / "SOUL.md").write_text("new identity", encoding="utf-8")
    (get_hermes_home() / "config.yaml").write_text("model:\n  default: x\n", encoding="utf-8")
    agent = SimpleNamespace(release_clients=lambda: None)
    runner = _make_runner(seeded, agent)

    reply = await runner._handle_reload_command(_event())

    assert reply.startswith("Reloaded")
    assert not _stored_prompt(seeded)
    assert SESSION_KEY not in runner._agent_cache
    assert [m["content"] for m in seeded.get_messages(SESSION_ID)] == ["hello", "hi"]


@pytest.mark.asyncio
async def test_broken_config_reports_error_and_changes_nothing(seeded):
    (get_hermes_home() / "config.yaml").write_text("model: [unclosed\n", encoding="utf-8")
    agent = SimpleNamespace(release_clients=lambda: None)
    runner = _make_runner(seeded, agent)

    reply = await runner._handle_reload_command(_event())

    assert "Reload failed" in reply and "config.yaml" in reply
    assert _stored_prompt(seeded) == "OLD PROMPT"
    assert runner._agent_cache[SESSION_KEY][0] is agent


@pytest.mark.asyncio
async def test_reload_keeps_process_only_env_vars(seeded, monkeypatch):
    monkeypatch.setenv("GATEWAY_ALLOW_ALL_USERS", "true")
    assert not (get_hermes_home() / ".env").exists()
    runner = _make_runner(seeded, SimpleNamespace(release_clients=lambda: None))

    reply = await runner._handle_reload_command(_event())

    assert reply.startswith("Reloaded")
    import os
    assert os.environ.get("GATEWAY_ALLOW_ALL_USERS") == "true"
