# Copyright (c) 2026-present Ngo Quoc Huy
# SPDX-License-Identifier: MIT

"""Live reload of a profile's on-disk files into a running conversation.

``/reload`` (CLI and gateway chats) shares this one code path.  The profile
files (SOUL.md, config.yaml, memories/, context files, skills) are re-read from
disk by the normal prompt builder on the next turn; this module only

1. validates the files first, so a broken file fails loudly and the previous
   good state (cached prompt, cached agent) is left untouched, and
2. invalidates the stored system prompt so the next turn rebuilds from disk
   instead of replaying the old bytes.

Session history is never touched.  The cost is one prompt-cache miss on the
next turn (the system prompt prefix changes); nothing is applied mid-turn.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Optional

logger = logging.getLogger(__name__)


@dataclass
class ProfileFilesCheck:
    """Result of validating the profile files a reload would re-read."""

    error: Optional[str] = None
    loaded: List[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return self.error is None


def check_profile_files(home: Path) -> ProfileFilesCheck:
    """Validate the profile files under ``home`` without applying anything."""
    import yaml

    result = ProfileFilesCheck()

    soul = home / "SOUL.md"
    if soul.is_file():
        try:
            soul.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError) as exc:
            result.error = f"SOUL.md is unreadable: {exc}"
            return result
        result.loaded.append("SOUL.md")

    config = home / "config.yaml"
    if config.is_file():
        try:
            parsed = yaml.safe_load(config.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, yaml.YAMLError) as exc:
            first_line = str(exc).strip().splitlines()[0] if str(exc).strip() else type(exc).__name__
            result.error = f"config.yaml is invalid: {first_line}"
            return result
        if parsed is not None and not isinstance(parsed, dict):
            result.error = "config.yaml is invalid: top level must be a mapping"
            return result
        result.loaded.append("config.yaml")

    for name in ("MEMORY.md", "USER.md"):
        memory_file = home / "memories" / name
        if memory_file.is_file():
            try:
                memory_file.read_text(encoding="utf-8")
            except (OSError, UnicodeDecodeError) as exc:
                result.error = f"memories/{name} is unreadable: {exc}"
                return result
            result.loaded.append(f"memories/{name}")

    return result


def invalidate_session_prompt(agent, session_db, session_id: Optional[str]) -> None:
    """Make the next turn rebuild the system prompt from disk.

    Clears the in-memory prompt on ``agent`` (when one is live) AND the
    persisted snapshot in the session DB: the turn loop replays the stored
    prompt verbatim for a continuing session, so clearing only the agent
    would be undone on the next turn.
    """
    from agent.prompt_builder import clear_skills_system_prompt_cache

    clear_skills_system_prompt_cache(clear_snapshot=True)
    if agent is not None:
        agent._invalidate_system_prompt()
    if session_db is not None and session_id:
        session_db.update_system_prompt(session_id, None)


def format_reload_report(check: ProfileFilesCheck) -> str:
    """One-line user-facing status for a successful reload."""
    files = ", ".join(check.loaded) if check.loaded else "no profile files found (defaults)"
    return (
        f"Reloaded {files}. The next message rebuilds the system prompt "
        "(one prompt-cache miss); conversation history is unchanged."
    )
