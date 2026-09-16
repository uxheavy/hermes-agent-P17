"""Profile-scoped exact skill target operations.

Inventory projection remains in :mod:`hermes_cli.profiles`; this module owns
full-content reads, approval-gated writes, and target-bound pending replay.
All operations use the canonical Hermes skill target resolver and public
``skill_manage`` lifecycle.
"""

from __future__ import annotations

import hashlib
import json
from contextlib import contextmanager
from pathlib import Path
from typing import Optional

from hermes_cli import profiles as _profiles

_PROFILE_SKILL_CONTENT_MAX_BYTES = _profiles._PROFILE_SKILL_CONTENT_MAX_BYTES
_PROFILE_SKILL_REVIEW_MAX_BYTES = _profiles._PROFILE_SKILL_REVIEW_MAX_BYTES
_PROFILE_SKILL_SUMMARY_MAX_BYTES = _profiles._PROFILE_SKILL_SUMMARY_MAX_BYTES
_PROFILE_SKILLS_MAX_ITEMS = _profiles._PROFILE_SKILLS_MAX_ITEMS


class ProfileSkillRevisionConflict(RuntimeError):
    """Raised when an exact skill target has changed since it was read."""


class ProfileSkillPendingError(ValueError):
    """Raised for malformed or unbound native profile skill approvals."""


@contextmanager
def _profile_skill_scope(name: str):
    """Bind all skill discovery, pending storage, and writes to one profile."""
    from hermes_constants import reset_hermes_home_override, set_hermes_home_override

    canon = _profiles.normalize_profile_name(name)
    _profiles.validate_profile_name(canon)
    profile_dir = _profiles.get_profile_dir(canon)
    if not profile_dir.is_dir() or (
        canon != "default" and _profiles.named_profile_is_deleted(profile_dir)
    ):
        raise FileNotFoundError(f"profile does not exist: {name}")
    token = set_hermes_home_override(str(profile_dir))
    try:
        yield canon, profile_dir
    finally:
        reset_hermes_home_override(token)


def _resolve_profile_skill_target(
    profile_dir: Path,
    owner_id: str,
    skill_id: str,
    expected_revision: Optional[str] = None,
) -> dict:
    from tools.skill_targets import _resolve_bound_skill_target

    target, error = _resolve_bound_skill_target(
        owner_id,
        skill_id,
        expected_revision or ("0" * 64),
        local_skills_dir=profile_dir / "skills",
    )
    if target is None:
        raise ProfileSkillPendingError(error or "skill target is unavailable")
    return target


def _bounded_skill_content(raw: bytes) -> str:
    if len(raw) > _PROFILE_SKILL_CONTENT_MAX_BYTES:
        raise ValueError("skill content exceeds the profile CLI response limit")
    try:
        return raw.decode("utf-8")
    except UnicodeDecodeError as error:
        raise ValueError("skill content is not valid UTF-8") from error


def read_profile_skill(
    name: str,
    owner_id: str,
    skill_id: str,
    expected_revision: Optional[str] = None,
) -> dict:
    """Read one exact external target and return the same bytes' revision."""
    from tools.skill_targets import _skill_mutation_lock

    with _profile_skill_scope(name) as (_canon, profile_dir):
        target = _resolve_profile_skill_target(
            profile_dir, owner_id, skill_id, expected_revision
        )
        with _skill_mutation_lock(target["skill_dir"]):
            raw = target["skill_md"].read_bytes()
            revision = hashlib.sha256(raw).hexdigest()
            if expected_revision is not None and revision != expected_revision.lower():
                raise ProfileSkillRevisionConflict("skill target is stale")
            content = _bounded_skill_content(raw)
        result = {
            "owner_id": target["owner_id"],
            "skill_id": target["skill_id"],
            "revision": revision,
            "content": content,
        }
        if len(json.dumps(result, ensure_ascii=False).encode("utf-8")) > (
            _PROFILE_SKILL_SUMMARY_MAX_BYTES
        ):
            raise ValueError("skill response exceeds the profile CLI response limit")
        return result


def _parse_skill_result(raw_result: str) -> dict:
    try:
        result = json.loads(raw_result)
    except (TypeError, ValueError) as error:
        raise RuntimeError("skill operation returned an invalid result") from error
    if not isinstance(result, dict):
        raise RuntimeError("skill operation returned an invalid result")
    result.pop("path", None)
    result.pop("skill_md", None)
    if len(json.dumps(result, ensure_ascii=False).encode("utf-8")) > (
        _PROFILE_SKILL_SUMMARY_MAX_BYTES
    ):
        raise RuntimeError("skill operation response exceeded the profile CLI limit")
    return result


def edit_profile_skill(
    name: str,
    owner_id: str,
    skill_id: str,
    expected_revision: str,
    content: str,
) -> dict:
    """Full-content edit through the public skill lifecycle and approval gate."""
    if not isinstance(content, str) or not content:
        raise ValueError("skill content is required")
    raw = content.encode("utf-8")
    if len(raw) > _PROFILE_SKILL_CONTENT_MAX_BYTES:
        raise ValueError("skill content exceeds the profile CLI response limit")
    with _profile_skill_scope(name) as (_canon, profile_dir):
        target = _resolve_profile_skill_target(
            profile_dir, owner_id, skill_id, expected_revision
        )
        from tools.skill_manager_tool import skill_manage

        result = _parse_skill_result(
            skill_manage(
                action="patch",
                name=target["skill_id"],
                content=content,
                owner_id=target["owner_id"],
                skill_id=target["skill_id"],
                expected_revision=target["expected_revision"],
            )
        )
        result.setdefault("owner_id", target["owner_id"])
        result.setdefault("skill_id", target["skill_id"])
        if result.get("staged"):
            result["content_revision"] = hashlib.sha256(raw).hexdigest()
            result["expected_revision"] = target["expected_revision"]
        return result


def _pending_target(record: dict) -> tuple[dict, str]:
    payload = record.get("payload")
    if not isinstance(payload, dict):
        raise ProfileSkillPendingError("pending skill payload is invalid")
    fields = {field: payload.get(field) for field in ("owner_id", "skill_id", "expected_revision")}
    if not all(isinstance(value, str) and value.strip() for value in fields.values()):
        raise ProfileSkillPendingError("pending skill is not target-bound")
    content = payload.get("content")
    if not isinstance(content, str) or not content:
        raise ProfileSkillPendingError("pending skill content is unavailable")
    return fields, hashlib.sha256(content.encode("utf-8")).hexdigest()


def _pending_projection(record: dict) -> dict:
    fields, content_revision = _pending_target(record)
    return {
        "id": str(record.get("id") or ""),
        "action": str(record.get("action") or ""),
        "summary": str(record.get("summary") or "")[:1024],
        "origin": str(record.get("origin") or "")[:64],
        "created_at": record.get("created_at"),
        **fields,
        "content_revision": content_revision,
    }


def _bounded_review(diff: str) -> dict:
    """Bound the canonical native review text without reclassifying it."""
    encoded = diff.encode("utf-8", errors="replace")
    if len(encoded) <= _PROFILE_SKILL_REVIEW_MAX_BYTES:
        return {"diff": diff, "truncated": False}
    prefix = encoded[:_PROFILE_SKILL_REVIEW_MAX_BYTES].decode(
        "utf-8", errors="ignore"
    )
    return {"diff": prefix, "truncated": True}


def read_profile_skill_pending(name: str, pending_id: Optional[str] = None) -> dict:
    """List or review target-bound pending skills in the profile's queue."""
    from tools import write_approval as approval

    with _profile_skill_scope(name):
        records = (
            [approval.get_pending(approval.SKILLS, pending_id)]
            if pending_id
            else approval.list_pending(approval.SKILLS)
        )
        if pending_id and records[0] is None:
            raise FileNotFoundError("pending skill write does not exist")
        projected = []
        for record in records:
            try:
                projected.append(_pending_projection(record))
            except ProfileSkillPendingError:
                continue
        if pending_id:
            if not projected:
                raise ProfileSkillPendingError("pending skill is not target-bound")
            review = _bounded_review(approval.skill_pending_diff(records[0]))
            return {"pending": projected[0], "review": review}
        truncated = len(projected) > _PROFILE_SKILLS_MAX_ITEMS
        bounded = []
        for item in projected[:_PROFILE_SKILLS_MAX_ITEMS]:
            candidate = {"items": [*bounded, item], "truncated": False}
            if len(
                json.dumps(candidate, ensure_ascii=False, separators=(",", ":"))
                .encode("utf-8")
            ) > _PROFILE_SKILL_SUMMARY_MAX_BYTES:
                truncated = True
                break
            bounded.append(item)
        return {"items": bounded, "truncated": truncated}


def _assert_pending_binding(
    record: dict,
    owner_id: str,
    skill_id: str,
    expected_revision: str,
    content_revision: Optional[str] = None,
) -> dict:
    fields, pending_content_revision = _pending_target(record)
    if any(
        fields[field] != value
        for field, value in {
            "owner_id": owner_id,
            "skill_id": skill_id,
            "expected_revision": expected_revision,
        }.items()
    ):
        raise ProfileSkillPendingError("pending skill target does not match the requested target")
    if content_revision is not None and pending_content_revision != content_revision:
        raise ProfileSkillPendingError("pending skill content does not match the reviewed bytes")
    return fields


def approve_profile_skill_pending(
    name: str,
    pending_id: str,
    owner_id: str,
    skill_id: str,
    expected_revision: str,
    content_revision: Optional[str] = None,
) -> dict:
    """Approve one exact target-bound pending write through replay."""
    from tools import write_approval as approval
    from tools.skill_manager_tool import apply_skill_pending

    with _profile_skill_scope(name):
        record = approval.get_pending(approval.SKILLS, pending_id)
        if record is None:
            raise FileNotFoundError("pending skill write does not exist")
        if content_revision is None:
            raise ProfileSkillPendingError(
                "approved skill content revision is required"
            )
        _assert_pending_binding(
            record, owner_id, skill_id, expected_revision, content_revision
        )
        result = _parse_skill_result(apply_skill_pending(record["payload"]))
        result["pending_id"] = pending_id
        if not result.get("success"):
            return result
        approval.discard_pending(approval.SKILLS, pending_id)
        result.update(
            owner_id=owner_id,
            skill_id=skill_id,
        )
        return result


def reject_profile_skill_pending(
    name: str,
    pending_id: str,
    owner_id: str,
    skill_id: str,
    expected_revision: str,
) -> dict:
    """Reject one exact target-bound pending write."""
    from tools import write_approval as approval

    with _profile_skill_scope(name):
        record = approval.get_pending(approval.SKILLS, pending_id)
        if record is None:
            raise FileNotFoundError("pending skill write does not exist")
        _assert_pending_binding(record, owner_id, skill_id, expected_revision)
        approval.discard_pending(approval.SKILLS, pending_id)
        return {
            "success": True,
            "rejected": True,
            "pending_id": pending_id,
            "owner_id": owner_id,
            "skill_id": skill_id,
        }



__all__ = [
    "ProfileSkillPendingError",
    "ProfileSkillRevisionConflict",
    "approve_profile_skill_pending",
    "edit_profile_skill",
    "read_profile_skill",
    "read_profile_skill_pending",
    "reject_profile_skill_pending",
]
