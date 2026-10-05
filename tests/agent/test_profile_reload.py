# Copyright (c) 2026-present Ngo Quoc Huy
# SPDX-License-Identifier: MIT

"""agent.profile_reload: validate-first reload of profile files.

Observable contracts: a SOUL.md / memory edit is visible to the prompt
builder after invalidation, a broken config.yaml is rejected before anything
is applied, and invalidation clears the persisted prompt while session
history stays intact.
"""

from agent.profile_reload import (
    check_profile_files,
    format_reload_report,
    invalidate_session_prompt,
)
from agent.prompt_builder import load_soul_md
from hermes_constants import get_hermes_home
from hermes_state import SessionDB


def test_soul_edit_is_visible_after_invalidation():
    home = get_hermes_home()
    (home / "SOUL.md").write_text("first identity", encoding="utf-8")
    assert load_soul_md() == "first identity"

    (home / "SOUL.md").write_text("second identity", encoding="utf-8")
    invalidate_session_prompt(None, None, None)

    assert load_soul_md() == "second identity"
    assert "SOUL.md" in check_profile_files(home).loaded


def test_memory_and_config_edits_are_reported_loaded():
    home = get_hermes_home()
    (home / "memories").mkdir(exist_ok=True)
    (home / "memories" / "MEMORY.md").write_text("fact", encoding="utf-8")
    (home / "config.yaml").write_text("model:\n  default: x\n", encoding="utf-8")

    check = check_profile_files(home)

    assert check.ok
    assert {"config.yaml", "memories/MEMORY.md"} <= set(check.loaded)
    assert "config.yaml" in format_reload_report(check)


def test_broken_config_is_rejected_before_applying():
    home = get_hermes_home()
    (home / "config.yaml").write_text("model: [unclosed\n", encoding="utf-8")

    check = check_profile_files(home)

    assert not check.ok
    assert check.error.startswith("config.yaml is invalid")


def test_non_mapping_config_is_rejected():
    home = get_hermes_home()
    (home / "config.yaml").write_text("- just\n- a list\n", encoding="utf-8")

    assert not check_profile_files(home).ok


def test_invalidate_clears_stored_prompt_and_agent_cache_keeps_history():
    db = SessionDB(get_hermes_home() / "state.db")
    try:
        db.create_session("s1", "cli")
        db.update_system_prompt("s1", "OLD PROMPT")
        db.append_message("s1", "user", "hello")

        class _Agent:
            _cached_system_prompt = "OLD PROMPT"
            _cached_system_prompt_static = "OLD PROMPT"

            def _invalidate_system_prompt(self):
                self._cached_system_prompt = None

        agent = _Agent()
        invalidate_session_prompt(agent, db, "s1")

        assert agent._cached_system_prompt is None
        assert not db.get_session("s1")["system_prompt"]
        assert [m["content"] for m in db.get_messages("s1")] == ["hello"]
    finally:
        db.close()
