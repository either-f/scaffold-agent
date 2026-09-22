"""Personal platform endpoints (B07-B10).

All responses are private/no-store. PATCH job fields reject explicit null except
source_url; application PUT preserves omitted fields and clears nullable fields
only when they are explicitly null. The caller runs ensure_personal_schema first.
"""
from __future__ import annotations

import json
import time
from datetime import datetime, timezone
from typing import Any, Literal
from urllib.parse import urlsplit

from fastapi import APIRouter, HTTPException, Query, Request, Response
from fastapi.exception_handlers import request_validation_exception_handler
from fastapi.exceptions import RequestValidationError
from fastapi.routing import APIRoute
from pydantic import BaseModel, ConfigDict, Field, StrictBool, field_validator, model_validator
from starlette.datastructures import MutableHeaders

from ..auth_store import AuthManager
from ..platform_store import PlatformStore
from ..personal_store import (
    ACTION_NAMES,
    application_history,
    get_action_state,
    get_entity,
    list_application_records,
    list_saved_targets,
    normalize_kind,
    save_application,
    set_action_states,
)


class _PrivateRoute(APIRoute):
    """Keep per-user details and errors out of shared/browser caches."""
    def get_route_handler(self):
        original = super().get_route_handler()

        async def handle(request: Request) -> Response:
            try:
                response = await original(request)
            except HTTPException as exc:
                headers = MutableHeaders(headers=exc.headers or {})
                headers["Cache-Control"] = "private, no-store"
                headers.add_vary_header("Authorization")
                exc.headers = dict(headers)
                raise
            except RequestValidationError as exc:
                response = await request_validation_exception_handler(request, exc)
            response.headers["Cache-Control"] = "private, no-store"
            response.headers.add_vary_header("Authorization")
            return response

        return handle


class JobCreateBody(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    title: str = Field(min_length=1, max_length=200)
    company: str = Field(default="", max_length=200)
    city: str = Field(default="", max_length=100)
    salary: str = Field(default="", max_length=100)
    description: str = Field(default="", max_length=5000)
    direction: str = Field(default="", max_length=100)
    grad_year: str = Field(default="", max_length=50)
    meta: str = Field(default="", max_length=500)
    reason: str = Field(default="", max_length=5000)
    tags: list[str] = Field(default_factory=list, max_length=30)
    match: int = Field(default=0, ge=0, le=100, strict=True)
    source_url: str | None = Field(default=None, max_length=2048)

    @field_validator("source_url")
    @classmethod
    def validate_source_url(cls, value: str | None) -> str | None:
        if not value:
            return None
        if any(char.isspace() or ord(char) < 32 or ord(char) == 127 for char in value) or "\\" in value:
            raise ValueError("source_url must not contain whitespace or backslashes")
        parsed = urlsplit(value)
        if parsed.scheme.lower() not in {"http", "https"} or not parsed.hostname:
            raise ValueError("source_url must be an http(s) URL with a host")
        if parsed.username is not None or parsed.password is not None:
            raise ValueError("source_url must not contain userinfo")
        # Accessing port also validates malformed or out-of-range ports.
        if parsed.port is not None and parsed.port == 0:
            raise ValueError("source_url port must be positive")
        return value

    @field_validator("tags")
    @classmethod
    def validate_tags(cls, value: list[str]) -> list[str]:
        tags = [tag.strip() for tag in value]
        if any(not tag or len(tag) > 50 for tag in tags):
            raise ValueError("tags must be nonempty strings of at most 50 characters")
        return tags


class JobPatchBody(JobCreateBody):
    # Defaults are excluded from PATCH; supplied values reuse the create validators.
    title: str | None = Field(default=None, min_length=1, max_length=200)

    @model_validator(mode="after")
    def reject_explicit_null(self) -> "JobPatchBody":
        for name in self.model_fields_set - {"source_url"}:
            if getattr(self, name) is None:
                raise ValueError(f"{name} must not be null")
        return self


class ActionStateBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    favorite: StrictBool | None = None
    watch: StrictBool | None = None
    read_later: StrictBool | None = None
    read: StrictBool | None = None

    @model_validator(mode="after")
    def reject_explicit_null(self) -> "ActionStateBody":
        if any(getattr(self, key) is None for key in self.model_fields_set):
            raise ValueError("supplied action states must be boolean")
        return self


class ApplicationBody(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    stage: Literal["saved", "preparing", "applied", "interviewing", "offer", "rejected", "archived"]
    next_step: str | None = Field(default=None, max_length=500)
    next_at: datetime | None = None
    note: str | None = Field(default=None, max_length=5000)
    next_step_done: StrictBool | None = None

    @field_validator("next_at", mode="before")
    @classmethod
    def require_iso_datetime(cls, value: Any) -> Any:
        if value is not None and (
            not isinstance(value, str) or len(value) < 11 or value[10] not in "Tt "
        ):
            raise ValueError("next_at must be an ISO datetime string or null")
        return value

    @field_validator("next_at")
    @classmethod
    def normalize_datetime(cls, value: datetime | None) -> datetime | None:
        return value.astimezone(timezone.utc) if value and value.tzinfo else value

    @model_validator(mode="after")
    def reject_null_done(self) -> "ApplicationBody":
        if "next_step_done" in self.model_fields_set and self.next_step_done is None:
            raise ValueError("next_step_done must be boolean")
        return self


def _current_uid(auth_manager: AuthManager | None, request: Request) -> int | None:
    if auth_manager is None:
        return None
    user = auth_manager.resolve_token(request.headers.get("Authorization"))
    return int(user["id"]) if user is not None else None


def _require_uid(auth_manager: AuthManager | None, request: Request) -> int:
    uid = _current_uid(auth_manager, request)
    if uid is None:
        raise HTTPException(status_code=401, detail="请先登录")
    return uid


def _actions_view(state: dict[str, bool]) -> dict[str, Any]:
    return {"actions": state, **state,
            "favorited": state["favorite"], "watched": state["watch"]}


def _presentation_origin(entity: dict[str, Any], is_demo: bool) -> str:
    origin = entity.get("origin") or "unknown"
    return "demo" if is_demo and origin == "unknown" else origin


def _unavailable_detail(kind: str, target_id: int, state: dict[str, bool]) -> dict[str, Any]:
    # Only identifiers from the caller's own action and a generic reason may survive.
    return {"kind": kind, "id": target_id, "available": False,
            "unavailable_reason": "内容已失效", **_actions_view(state)}


def _detail(store: PlatformStore, kind: str, entity: dict[str, Any],
            uid: int | None, state: dict[str, bool] | None = None, *,
            is_demo: bool = False) -> dict[str, Any]:
    item = dict(entity)
    item["kind"] = kind
    item["available"] = True
    item["origin"] = _presentation_origin(item, is_demo)
    item["source_id"] = item.get("source_platform") or None
    for field in ("source_platform", "external_id", "source_url", "published_at", "fetched_at"):
        item[field] = item.get(field) or None
    if kind == "job":
        item["description"] = item.get("reason", "")
        item["match"] = (item.get("match") or None) if is_demo else None
        item["referral"] = bool(item["is_referral"])
    elif kind == "project":
        item["score"] = (item.get("score") or None) if is_demo else None
    else:
        item["description"] = item.get("summary", "")
        item["importance"] = (item.get("importance") or None) if is_demo else None
        item["score"] = item["importance"]
    if state is None:
        state = get_action_state(store, kind, int(item["id"]), uid)
    item.update(_actions_view(state))
    return item


def _get_or_404(store: PlatformStore, kind: str, target_id: int,
                uid: int | None) -> dict[str, Any]:
    entity = get_entity(store, kind, target_id, uid)
    if entity is None:
        raise HTTPException(status_code=404, detail="target not found")
    return entity


def _insert_manual_job(store: PlatformStore, body: JobCreateBody, uid: int) -> int:
    description = body.description if "description" in body.model_fields_set else body.reason
    meta = body.meta or " · ".join(value for value in (body.company, body.city, body.salary) if value)
    now = time.time()
    with store._write_transaction():
        cursor = store.conn.execute(
            "INSERT INTO jobs(company,logo,logo_class,title,city,direction,grad_year,meta,"
            "salary,match,tags,reason,is_referral,source_logo,created_at,updated_at,"
            "source_url,origin,created_by) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (body.company, "手", "l-blue", body.title, body.city, body.direction, body.grad_year,
             meta, body.salary, body.match, json.dumps(body.tags, ensure_ascii=False), description,
             0, "", now, now, body.source_url, "manual", uid),
        )
        return int(cursor.lastrowid)


def _patch_manual_job(store: PlatformStore, job_id: int, body: JobPatchBody, uid: int) -> dict:
    data = body.model_dump(exclude_unset=True)
    if "description" in data:
        data["reason"] = data.pop("description")
    if "tags" in data:
        data["tags"] = json.dumps(data["tags"], ensure_ascii=False)
    with store._write_transaction():
        entity = _get_or_404(store, "job", job_id, uid)
        if entity.get("origin") != "manual":
            raise HTTPException(status_code=403, detail="仅本人手动岗位可编辑")
        if data:
            # Keep the old list's display metadata aligned when basic job fields change.
            if "meta" not in data and {"company", "city", "salary"} & data.keys():
                data["meta"] = " · ".join(
                    data.get(key, entity[key]) for key in ("company", "city", "salary")
                    if data.get(key, entity[key])
                )
            data["updated_at"] = time.time()
            assignments = ", ".join(f"{key}=?" for key in data)
            store.conn.execute(
                f"UPDATE jobs SET {assignments} WHERE id=? AND created_by=? AND origin='manual'",
                (*data.values(), job_id, uid),
            )
        return _get_or_404(store, "job", job_id, uid)


def _application_view(store: PlatformStore, record: dict[str, Any],
                      job: dict[str, Any], uid: int, *, is_demo: bool = False) -> dict[str, Any]:
    history = application_history(store, record.get("id"))
    for event in history:
        event["next_step_done"] = bool(event["next_step_done"])
    return {
        "id": record.get("id"), "job_id": record["job_id"],
        "job": _detail(store, "job", job, uid, is_demo=is_demo), "stage": record["stage"],
        "next_step": record.get("next_step"), "next_at": record.get("next_at"),
        "note": record.get("note"), "next_step_done": bool(record.get("next_step_done")),
        "created_at": record.get("created_at"), "updated_at": record.get("updated_at"),
        "history": history,
    }


def create_personal_router(store: PlatformStore, auth_manager: AuthManager | None = None) -> APIRouter:
    """Create B07-B10 endpoints after ensure_personal_schema(store)."""
    router = APIRouter(route_class=_PrivateRoute)
    is_demo = auth_manager is None or auth_manager.settings.dev_mode

    @router.get("/api/platform/jobs/{job_id}")
    def job_detail(job_id: int, request: Request) -> dict[str, Any]:
        uid = _current_uid(auth_manager, request)
        return _detail(store, "job", _get_or_404(store, "job", job_id, uid), uid, is_demo=is_demo)

    @router.get("/api/platform/projects/{project_id}")
    def project_detail(project_id: int, request: Request) -> dict[str, Any]:
        uid = _current_uid(auth_manager, request)
        return _detail(store, "project", _get_or_404(store, "project", project_id, uid), uid, is_demo=is_demo)

    @router.get("/api/platform/news/{news_id}")
    def news_detail(news_id: int, request: Request) -> dict[str, Any]:
        uid = _current_uid(auth_manager, request)
        return _detail(store, "news", _get_or_404(store, "news", news_id, uid), uid, is_demo=is_demo)

    @router.post("/api/platform/jobs", status_code=201)
    def create_job(body: JobCreateBody, request: Request) -> dict[str, Any]:
        uid = _require_uid(auth_manager, request)
        job_id = _insert_manual_job(store, body, uid)
        return _detail(store, "job", _get_or_404(store, "job", job_id, uid), uid, is_demo=is_demo)

    @router.patch("/api/platform/jobs/{job_id}")
    def update_job(job_id: int, body: JobPatchBody, request: Request) -> dict[str, Any]:
        uid = _require_uid(auth_manager, request)
        return _detail(store, "job", _patch_manual_job(store, job_id, body, uid), uid, is_demo=is_demo)

    @router.get("/api/platform/my/items")
    def my_items(request: Request, kind: str | None = None, action: str | None = None,
                 limit: int = Query(50, ge=1, le=100), offset: int = Query(0, ge=0)) -> dict[str, Any]:
        uid = _require_uid(auth_manager, request)
        stable_kind = normalize_kind(kind) if kind else None
        if kind and stable_kind is None:
            raise HTTPException(status_code=422, detail="kind must be job, project or news")
        if action and action not in ACTION_NAMES:
            raise HTTPException(status_code=422, detail="unsupported action")
        targets = list_saved_targets(store, uid, kind=stable_kind, action=action or None,
                                     limit=limit, offset=offset)
        items = []
        for target in targets:
            entity = get_entity(store, target["kind"], target["id"], uid)
            if entity is not None:
                detail = _detail(store, target["kind"], entity, uid, is_demo=is_demo)
            else:
                detail = _unavailable_detail(target["kind"], target["id"],
                                             get_action_state(store, target["kind"], target["id"], uid))
            items.append({**detail, "entity": detail})
        return {"items": items}

    @router.put("/api/platform/my/items/{kind}/{target_id}/actions")
    def set_actions(kind: str, target_id: int, body: ActionStateBody, request: Request) -> dict[str, Any]:
        uid = _require_uid(auth_manager, request)
        stable_kind = normalize_kind(kind)
        if stable_kind is None:
            raise HTTPException(status_code=422, detail="kind must be job, project or news")
        try:
            state = set_action_states(store, stable_kind, target_id, uid, body.model_dump(exclude_unset=True))
        except LookupError as exc:
            raise HTTPException(status_code=404, detail="target not found") from exc
        entity = get_entity(store, stable_kind, target_id, uid)
        detail = (_detail(store, stable_kind, entity, uid, state, is_demo=is_demo) if entity is not None
                  else _unavailable_detail(stable_kind, target_id, state))
        return {"item": detail, **_actions_view(state)}

    @router.get("/api/platform/applications")
    def applications(request: Request) -> dict[str, Any]:
        uid = _require_uid(auth_manager, request)
        items = []
        for record in list_application_records(store, uid):
            job = get_entity(store, "job", int(record["job_id"]), uid)
            if job is not None:
                items.append(_application_view(store, record, job, uid, is_demo=is_demo))
        return {"items": items}

    @router.put("/api/platform/jobs/{job_id}/application")
    def set_application(job_id: int, body: ApplicationBody, request: Request) -> dict[str, Any]:
        uid = _require_uid(auth_manager, request)
        # exclude_unset follows model_fields_set, preserving explicit null and omissions.
        changes = body.model_dump(exclude_unset=True, mode="json")
        try:
            record = save_application(store, uid, job_id, **changes)
        except LookupError as exc:
            raise HTTPException(status_code=404, detail="target not found") from exc
        job = _get_or_404(store, "job", job_id, uid)
        return _application_view(store, record, job, uid, is_demo=is_demo)

    return router
