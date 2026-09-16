"""Exact target resolution and mutation coordination for shared skills.

The skill manager keeps legacy name lookup and lifecycle behavior. This module
owns the target-bound contract: opaque root identity, normalized relative skill
identity, content revisions, canonical configured-root validation, and the
shared package lock used by participating native writers.
"""

import hashlib
import re
from contextlib import contextmanager
from pathlib import Path, PureWindowsPath
from typing import Any, Dict, Iterator, List, Optional, Tuple

from hermes_cli.active_sessions import _FileLock


_SKILL_REVISION_RE = re.compile(r"^[0-9a-fA-F]{64}$")


@contextmanager
def _skill_mutation_lock(skill_dir: Path) -> Iterator[None]:
    """Serialize participating native mutations for one skill package.

    The lock lives beside the canonical package so separate profiles that
    share an external root coordinate on the same target.
    """
    skill_dir = Path(skill_dir).resolve()
    if not skill_dir.is_dir():
        raise FileNotFoundError(f"Skill directory no longer exists: {skill_dir}")

    # Keep the lock beside the package rather than inside it: _FileLock creates
    # its parent, and a removed package must never be recreated by a writer.
    lock_path = skill_dir.parent / f".{skill_dir.name}.hermes-skill-manager.lock"
    with _FileLock(lock_path):
        if not skill_dir.is_dir():
            raise FileNotFoundError(f"Skill directory no longer exists: {skill_dir}")
        yield


def _skill_revision(path: Path) -> str:
    """Return the SHA256 revision used by the target-bound contract."""
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _skill_root_owner_id(root: Path) -> str:
    """Return an opaque, deterministic identity for a configured root."""
    return hashlib.sha256(str(root.resolve()).encode("utf-8")).hexdigest()


def _stale_skill_result(
    skill_path: Path,
    expected_revision: str,
    actual_revision: Optional[str],
    owner_id: Optional[str] = None,
    skill_id: Optional[str] = None,
) -> Dict[str, Any]:
    result = {
        "success": False,
        "stale": True,
        "error": (
            f"Skill target is stale: expected revision {expected_revision}, "
            f"current revision is {actual_revision or 'missing'}. Re-read the "
            "target and retry with its current revision."
        ),
        "path": str(skill_path),
        "expected_revision": expected_revision,
        "actual_revision": actual_revision,
    }
    if owner_id is not None and skill_id is not None:
        result.update(owner_id=owner_id, skill_id=skill_id)
        result.pop("path", None)
    return result


def _target_fields_supplied(
    owner_id: Optional[str],
    skill_id: Optional[str],
    expected_revision: Optional[str],
) -> bool:
    return any(value is not None for value in (owner_id, skill_id, expected_revision))


def _resolve_bound_skill_target(
    owner_id: Optional[str],
    skill_id: Optional[str],
    expected_revision: Optional[str],
    *,
    local_skills_dir: Path,
) -> Tuple[Optional[Dict[str, Any]], Optional[str]]:
    """Resolve an exact skill package under one configured external root.

    owner_id is an opaque digest of the configured external root's canonical
    path. skill_id identifies a package path relative to that root; the
    optional trailing SKILL.md is accepted for callers that already have the
    file path.
    """
    if not all(
        isinstance(value, str) and value.strip()
        for value in (owner_id, skill_id, expected_revision)
    ):
        return None, (
            "owner_id, skill_id, and expected_revision are required for a "
            "target-bound edit."
        )
    owner_id = owner_id.strip()
    skill_id = skill_id.strip()
    expected_revision = expected_revision.strip()
    if not _SKILL_REVISION_RE.fullmatch(expected_revision):
        return None, "expected_revision must be a 64-character SHA256 hex digest."

    # Treat both separators as path separators so a Windows-shaped traversal
    # cannot pass through a POSIX test/runtime unchanged.
    if (
        Path(skill_id).is_absolute()
        or PureWindowsPath(skill_id).is_absolute()
        or PureWindowsPath(skill_id).drive
        or skill_id.startswith(("/", "\\"))
    ):
        return None, "skill_id must be relative to its configured external root."
    parts = skill_id.replace("\\", "/").split("/")
    if any(part in {"", ".", ".."} for part in parts):
        return None, "skill_id must be a normalized relative path without traversal."
    if not parts:
        return None, "skill_id is required."

    from agent.skill_utils import (
        get_all_skills_dirs,
        get_project_skills_dirs,
        is_org_mirror_path,
        iter_skill_index_files,
    )

    roots: List[Path] = []
    local_root = Path(local_skills_dir).resolve()
    try:
        project_roots = {Path(path).resolve() for path in get_project_skills_dirs()}
    except (OSError, RuntimeError):
        project_roots = set()
    for configured in get_all_skills_dirs()[1:]:
        try:
            root = Path(configured).resolve()
        except (OSError, RuntimeError):
            continue
        if root == local_root or root in roots or root in project_roots:
            continue
        try:
            root.relative_to(local_root)
            continue
        except ValueError:
            pass
        roots.append(root)
    root = next(
        (
            candidate
            for candidate in roots
            if owner_id == _skill_root_owner_id(candidate)
        ),
        None,
    )
    if root is None:
        return None, "owner_id must identify a configured external skills root."

    relative = Path(*parts)
    target_path = root / relative
    if target_path.name != "SKILL.md":
        target_path = target_path / "SKILL.md"
    try:
        resolved_file = target_path.resolve()
        resolved_file.relative_to(root)
    except (OSError, RuntimeError, ValueError):
        return None, "skill_id resolves outside its configured external root."
    skill_dir = resolved_file.parent
    if is_org_mirror_path(skill_dir, local_root) or "_org" in relative.parts:
        return None, "target-bound edits cannot address organisation mirrors."
    if any(
        skill_dir == project_root or project_root in skill_dir.parents
        for project_root in project_roots
    ):
        return None, "target-bound edits cannot address project skills."
    if not resolved_file.is_file():
        return None, f"Skill target was not found: {skill_id}."
    indexed_files = {
        candidate.resolve() for candidate in iter_skill_index_files(root, "SKILL.md")
    }
    if resolved_file not in indexed_files:
        return (
            None,
            "skill_id must identify an indexed skill package under its configured root.",
        )
    return {
        "owner_id": owner_id,
        "skill_id": "/".join(parts),
        "expected_revision": expected_revision.lower(),
        "root": root,
        "skill_dir": skill_dir,
        "skill_md": resolved_file,
    }, None
