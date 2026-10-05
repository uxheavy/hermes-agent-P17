# Copyright (c) 2026-present Ngo Quoc Huy
# SPDX-License-Identifier: MIT

"""CLI ``/reload``: validate first, then drop the stored prompt and live agent.

A broken config.yaml keeps the current agent and stored prompt; a good reload
clears both while conversation_history stays on the CLI object.
"""

from types import SimpleNamespace

from cli import HermesCLI
from hermes_constants import get_hermes_home
from hermes_state import SessionDB


def _make_cli(db):
    cli = HermesCLI.__new__(HermesCLI)
    cli.agent = SimpleNamespace(_invalidate_system_prompt=lambda: None)
    cli.session_id = "cli-session"
    cli._session_db = db
    cli.conversation_history = [{"role": "user", "content": "hello"}]
    return cli


def _db():
    db = SessionDB(get_hermes_home() / "state.db")
    db.create_session("cli-session", "cli")
    db.update_system_prompt("cli-session", "OLD PROMPT")
    return db


def test_reload_drops_agent_and_stored_prompt_keeps_history(capsys):
    db = _db()
    try:
        (get_hermes_home() / "SOUL.md").write_text("new identity", encoding="utf-8")
        cli = _make_cli(db)

        cli._reload_profile()

        assert cli.agent is None
        assert not db.get_session("cli-session")["system_prompt"]
        assert cli.conversation_history == [{"role": "user", "content": "hello"}]
        assert "Reloaded" in capsys.readouterr().out
    finally:
        db.close()


def test_broken_config_keeps_agent_and_stored_prompt(capsys):
    db = _db()
    try:
        (get_hermes_home() / "config.yaml").write_text("model: [unclosed\n", encoding="utf-8")
        cli = _make_cli(db)
        agent = cli.agent

        cli._reload_profile()

        assert cli.agent is agent
        assert db.get_session("cli-session")["system_prompt"] == "OLD PROMPT"
        assert "Reload failed" in capsys.readouterr().out
    finally:
        db.close()
