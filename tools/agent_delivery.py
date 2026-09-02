# Copyright (c) 2026-present Ngo Quoc Huy
# SPDX-License-Identifier: MIT

# Copyright (c) 2025 Nous Research
# SPDX-License-Identifier: MIT

"""Shared local Hermes delivery command construction."""

from __future__ import annotations

import shutil
import sys
from pathlib import Path


def resolve_hermes_cli(*, which=shutil.which) -> str:
    """Resolve the Hermes executable for the current runtime.

    Delivery runs from a service process where PATH is not a reliable source
    of the matching install. A missing executable is an actionable error, not
    a command that should fail later in a detached worker.
    """
    executable = Path(sys.executable or "")
    sibling = executable.parent / (
        "hermes.exe" if sys.platform == "win32" else "hermes"
    )
    if sibling.is_file():
        return str(sibling)
    found = which("hermes")
    if found:
        return found
    raise FileNotFoundError(
        "Hermes executable not found beside the active interpreter or on PATH"
    )


def local_delivery_args(profile: str, *, cli: str | None = None) -> list[str]:
    """Build argv for one local profile's canonical Bot Chat delivery."""
    return [
        cli or resolve_hermes_cli(),
        "-p",
        profile,
        "chat",
        "--in",
        "~",
        "-c",
        "Bot Chat",
        "--create-if-missing",
        "-Q",
    ]


def local_delivery_command(
    profile: str, query_file: str, *, cli: str | None = None
) -> list[str]:
    """Build argv for local delivery with a file-backed message body."""
    return [*local_delivery_args(profile, cli=cli), "--query-file", query_file]
