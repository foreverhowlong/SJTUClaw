"""Skill discovery and session-scoped usage transport."""

from __future__ import annotations

from fastapi import APIRouter, Request


router = APIRouter(tags=["skills"])


@router.get("/api/skills")
async def list_skills(request: Request) -> dict[str, list[dict[str, str]]]:
    skills = request.app.state.runtime.skill_registry.list()
    return {"skills": [skill.to_dict() for skill in skills]}


@router.get("/api/skills/{name}")
async def get_skill(name: str, request: Request) -> dict[str, object]:
    package = request.app.state.runtime.skill_registry.get(name)
    return {
        **package.summary.to_dict(),
        "resources": [resource.path for resource in package.resources],
    }


@router.get("/api/sessions/{session_id}/skill-usages")
async def list_skill_usages(session_id: str, request: Request) -> dict[str, object]:
    session = request.app.state.runtime.session_store.load(session_id)
    return {"usages": [usage.to_dict() for usage in session.skill_usages]}
