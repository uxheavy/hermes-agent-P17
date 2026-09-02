# Copyright (c) 2026-present Ngo Quoc Huy
# SPDX-License-Identifier: MIT

# Copyright (c) 2025 Nous Research
# SPDX-License-Identifier: MIT

"""Profile identity and local roster primitives shared by agent transports."""

from __future__ import annotations

from pathlib import Path

from hermes_constants import get_hermes_home


def current_hermes_home() -> Path:
    """Return the active profile home through Hermes' canonical resolver."""
    return get_hermes_home()


def hermes_root(home: Path) -> Path:
    """Return the install root for either the default or a named profile."""
    if home.parent.name == "profiles":
        return home.parent.parent
    return home


def profile_name(home: Path) -> str:
    """Return the durable profile name represented by *home*."""
    if home.parent.name == "profiles":
        return home.name
    return "default"


def local_profiles(root: Path) -> list[tuple[str, Path]]:
    """Return the default profile and every named profile on this install."""
    entries: list[tuple[str, Path]] = [("default", root)]
    try:
        profiles = root / "profiles"
        if profiles.is_dir():
            entries.extend(
                (child.name, child)
                for child in sorted(profiles.iterdir())
                if child.is_dir()
            )
    except OSError:
        pass
    return entries


def profile_names(root: Path) -> list[str]:
    """Return local profile names in roster order."""
    return [name for name, _path in local_profiles(root)]


def profile_handle(name: str) -> str:
    """Return the mention handle for a local profile."""
    return "hermes" if name == "default" else name


def resolve_profile_name(target: str, roster: list[str]) -> str | None:
    """Resolve a case-insensitive handle to a profile name."""
    want = target.strip()
    if not want:
        return None
    if want.lower() == "hermes":
        return "default" if "default" in roster else None
    return next((name for name in roster if name.lower() == want.lower()), None)


def registered_peer_names(root: Path) -> list[str]:
    """Return configured peer names without coupling callers to YAML storage."""
    try:
        config_path = root / "config.yaml"
        if not config_path.is_file():
            return []
        raw = config_path.read_text(encoding="utf-8", errors="replace")
        if "bot_peers" not in raw:
            return []
        import yaml

        data = yaml.safe_load(raw)
        peers = data.get("bot_peers") if isinstance(data, dict) else None
        if not isinstance(peers, dict):
            return []
        return sorted(str(name) for name in peers if str(name).strip())
    except Exception:
        return []
