"""Admin web section for the active knowledge pack: login-gated FAQ management.
Every route below except /admin/login checks the session itself -- there is no
middleware-level gate, so a new route added here must remember to call
require_admin() as its first line."""
from __future__ import annotations

import datetime

import bcrypt
from fastapi import APIRouter, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

import db
import faq_state
import pack_config
import runtime_state

router = APIRouter(prefix="/admin")
templates = Jinja2Templates(directory="templates")


def require_admin(request: Request) -> str | None:
    """Returns the logged-in admin's username, or None if not authenticated --
    callers redirect to /admin/login themselves on None (kept explicit, not a
    dependency-injected exception, so it's obvious at each route that nothing
    below this line runs for an unauthenticated request)."""
    return request.session.get("admin_username")


@router.get("/login", response_class=HTMLResponse)
async def login_form(request: Request):
    if require_admin(request):
        return RedirectResponse("/admin", status_code=303)
    return templates.TemplateResponse(request, "login.html", {"error": None})


@router.post("/login", response_class=HTMLResponse)
async def login_submit(request: Request, username: str = Form(...), password: str = Form(...)):
    row = await db.get_admin_by_username(username)
    if row is None or not bcrypt.checkpw(password.encode("utf-8"), row["password_hash"].encode("utf-8")):
        return templates.TemplateResponse(request, "login.html", {"error": "Invalid username or password"}, status_code=401)
    request.session["admin_username"] = username
    return RedirectResponse("/admin", status_code=303)


@router.get("/logout")
async def logout(request: Request):
    request.session.clear()
    return RedirectResponse("/admin/login", status_code=303)


@router.get("", response_class=HTMLResponse)
async def dashboard(request: Request):
    username = require_admin(request)
    if not username:
        return RedirectResponse("/admin/login", status_code=303)
    faqs = await db.list_faqs(pack_config.EVENT_PACK)
    summary = await db.get_analytics_summary(pack_config.EVENT_PACK)
    return templates.TemplateResponse(request, "dashboard.html", {
        "username": username,
        "pack_id": pack_config.EVENT_PACK,
        "faqs": faqs,
        "summary": summary,
        "msg": request.query_params.get("msg"),
        "paused": runtime_state.PAUSED,
    })


@router.post("/pause")
async def pause_api(request: Request):
    if not require_admin(request):
        return RedirectResponse("/admin/login", status_code=303)
    await runtime_state.set_paused(True)
    return RedirectResponse("/admin?msg=API+paused", status_code=303)


@router.post("/resume")
async def resume_api(request: Request):
    if not require_admin(request):
        return RedirectResponse("/admin/login", status_code=303)
    await runtime_state.set_paused(False)
    return RedirectResponse("/admin?msg=API+resumed", status_code=303)


@router.get("/devices", response_class=HTMLResponse)
async def devices_page(request: Request):
    username = require_admin(request)
    if not username:
        return RedirectResponse("/admin/login", status_code=303)
    devices = await db.list_devices(pack_config.EVENT_PACK)
    return templates.TemplateResponse(request, "devices.html", {
        "username": username,
        "pack_id": pack_config.EVENT_PACK,
        "devices": devices,
    })


@router.get("/cost", response_class=HTMLResponse)
async def cost_dashboard(request: Request):
    username = require_admin(request)
    if not username:
        return RedirectResponse("/admin/login", status_code=303)

    day = request.query_params.get("day") or datetime.date.today().isoformat()
    cost_summary = await db.get_cost_summary(pack_config.EVENT_PACK)
    hourly = await db.get_hourly_usage(pack_config.EVENT_PACK, datetime.date.fromisoformat(day))

    return templates.TemplateResponse(request, "cost.html", {
        "username": username,
        "pack_id": pack_config.EVENT_PACK,
        "cost": cost_summary,
        "hourly": hourly,
        "day": day,
        "today": datetime.date.today().isoformat(),
    })


@router.get("/faq/new", response_class=HTMLResponse)
async def faq_new_form(request: Request):
    if not require_admin(request):
        return RedirectResponse("/admin/login", status_code=303)
    return templates.TemplateResponse(request, "faq_new.html", {"error": None})


@router.post("/faq/new")
async def faq_new_submit(request: Request, faq_id: str = Form(...), category: str = Form(...)):
    username = require_admin(request)
    if not username:
        return RedirectResponse("/admin/login", status_code=303)
    faq_id = faq_id.strip().lower().replace(" ", "_")
    if not faq_id:
        return templates.TemplateResponse(request, "faq_new.html", {"error": "FAQ ID is required"}, status_code=400)
    await db.create_faq(pack_config.EVENT_PACK, faq_id, category, username)
    return RedirectResponse(f"/admin/faq/{faq_id}", status_code=303)


@router.get("/faq/{faq_id}", response_class=HTMLResponse)
async def faq_edit_form(request: Request, faq_id: str):
    if not require_admin(request):
        return RedirectResponse("/admin/login", status_code=303)
    faq = await db.get_faq_detail(pack_config.EVENT_PACK, faq_id)
    if faq is None:
        return RedirectResponse("/admin?msg=FAQ+not+found", status_code=303)
    return templates.TemplateResponse(request, "faq_edit.html", {
        "faq": faq,
        "languages": pack_config.SUPPORTED_LANGUAGES,
        "msg": request.query_params.get("msg"),
    })


@router.post("/faq/{faq_id}")
async def faq_edit_submit(request: Request, faq_id: str):
    username = require_admin(request)
    if not username:
        return RedirectResponse("/admin/login", status_code=303)

    form = await request.form()
    category = form.get("category", "static")
    status = form.get("status", "approved")
    await db.upsert_faq(pack_config.EVENT_PACK, faq_id, category, status, username)

    for lang in pack_config.SUPPORTED_LANGUAGES:
        keywords_raw = form.get(f"keywords_{lang}")
        if keywords_raw is not None:
            keywords = [k.strip() for k in keywords_raw.split(",") if k.strip()]
            await db.replace_faq_keywords(pack_config.EVENT_PACK, faq_id, lang, keywords)

        answer_raw = form.get(f"answer_{lang}")
        if answer_raw and answer_raw.strip():
            await db.upsert_faq_answer(pack_config.EVENT_PACK, faq_id, lang, answer_raw.strip(), username)

    await faq_state.reload()
    return RedirectResponse(f"/admin/faq/{faq_id}?msg=Saved", status_code=303)
