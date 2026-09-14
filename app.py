from pathlib import Path
import traceback
from typing import Optional

import uvicorn
from fastapi import FastAPI, Request, Depends, UploadFile, File
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel, Field

import database
import auth
from backend import run_travel_agent, resume_travel_agent
from tools.voice_tool import transcribe_audio

# backend.py's node functions call asyncio.run() internally (for MCP tool
# calls). Since FastAPI's async endpoints already run inside uvicorn's
# event loop, calling asyncio.run() from deep inside that call stack would
# normally raise "asyncio.run() cannot be called from a running event
# loop". nest_asyncio patches this so the existing synchronous
# run_travel_agent()/resume_travel_agent() functions can call async MCP
# helpers without a full async rewrite of backend.py.
import nest_asyncio
nest_asyncio.apply()

BASE_DIR = Path(__file__).resolve().parent

app = FastAPI(
    title="TripCrew",
    description=(
        "LangGraph Multi-Agent Travel Planner with Supervisor, Guardrails, "
        "Human-in-the-Loop, MCP tools, auth, and voice input"
    ),
    version="2.0.0"
)

app.mount(
    "/static",
    StaticFiles(directory=str(BASE_DIR / "static")),
    name="static"
)

templates = Jinja2Templates(
    directory=str(BASE_DIR / "templates")
)


@app.on_event("startup")
def on_startup():
    # Creates the users/trips tables if they don't exist yet.
    # Safe to run on every startup.
    database.init_db()


class TravelRequest(BaseModel):
    message: str
    thread_id: str | None = None


class ApprovalRequest(BaseModel):
    thread_id: str = Field(min_length=1)
    approved: bool
    feedback: str = ""


@app.get("/", response_class=HTMLResponse)
async def home(request: Request):
    return templates.TemplateResponse(
        request=request,
        name="index.html",
        context={}
    )


# =========================
# Auth
# =========================

@app.post("/api/auth/register", response_model=auth.Token)
async def register(payload: auth.UserCreate):
    return auth.register_user(payload)


@app.post("/api/auth/login", response_model=auth.Token)
async def login(payload: auth.UserLogin):
    return auth.login_user(payload)


@app.get("/api/auth/me", response_model=auth.UserOut)
async def me(current_user: auth.UserOut = Depends(auth.get_current_user_required)):
    return current_user


# =========================
# Trip history (requires login)
# =========================

@app.get("/api/trips")
async def list_trips(current_user: auth.UserOut = Depends(auth.get_current_user_required)):
    trips = database.get_user_trips(current_user.id)
    return {"success": True, "trips": trips}


@app.delete("/api/trips/{trip_id}")
async def delete_trip(
    trip_id: str,
    current_user: auth.UserOut = Depends(auth.get_current_user_required)
):
    deleted = database.delete_trip(current_user.id, trip_id)

    if not deleted:
        return JSONResponse(
            status_code=404,
            content={"success": False, "error": "Trip not found."}
        )

    return {"success": True}


# =========================
# Travel planner (guest-friendly: works with or without a token)
#
# This starts a new (or continues an existing) planning thread. It will
# usually pause partway through and return requires_approval: True with a
# draft itinerary — the frontend must then call /api/travel/approve to
# either finalize it or send back revision feedback.
#
# A trip is NOT saved to history here, even for logged-in users — only
# once it's actually finalized via /api/travel/approve. A pending draft
# isn't "the trip" yet.
# =========================

@app.post("/api/travel")
async def travel_planner(
    request_data: TravelRequest,
    current_user: Optional[auth.UserOut] = Depends(auth.get_current_user_optional),
):
    try:
        user_message = request_data.message.strip()
        if not user_message:
            return JSONResponse(
                status_code=400,
                content={
                    "success": False,
                    "error": "Message cannot be empty."
                }
            )

        result = run_travel_agent(
            user_input=user_message,
            thread_id=request_data.thread_id
        )

        return JSONResponse(
            content={
                "success": True,
                **result,
            }
        )
    except Exception as e:
        print("ERROR:", e)
        traceback.print_exc()
        return JSONResponse(
            status_code=500,
            content={
                "success": False,
                "error": str(e)
            }
        )


# =========================
# Human-in-the-loop approval / revision
#
# Called after a /api/travel response comes back with requires_approval:
# True. approved=True finalizes the plan (-> final_agent). approved=False
# loops back to itinerary_agent for a revision using the given feedback,
# then pauses for approval again (capped by MAX_REVISIONS in backend.py).
#
# A trip is saved to a logged-in user's history only once this call
# returns requires_approval: False and guardrail_allowed: True — i.e.
# the plan is genuinely finalized, not just another draft round.
# =========================

@app.post("/api/travel/approve")
async def approve_travel_plan(
    request_data: ApprovalRequest,
    current_user: Optional[auth.UserOut] = Depends(auth.get_current_user_optional),
):
    try:
        if not request_data.approved and not request_data.feedback.strip():
            return JSONResponse(
                status_code=400,
                content={
                    "success": False,
                    "error": "Please provide revision feedback when rejecting the draft.",
                },
            )

        result = resume_travel_agent(
            thread_id=request_data.thread_id,
            approved=request_data.approved,
            feedback=request_data.feedback,
        )

        # Only save once the plan is genuinely finalized (no longer paused
        # for approval) and it wasn't a guardrail-blocked request. A user
        # mid-revision-loop hasn't produced "the trip" yet.
        if (
            current_user
            and not result.get("requires_approval", False)
            and result.get("guardrail_allowed", True)
        ):
            title = (result.get("user_query") or "").strip()[:80] or "Untitled trip"
            database.save_trip(current_user.id, result["thread_id"], title)

        return JSONResponse(
            content={
                "success": True,
                **result,
            }
        )
    except Exception as exc:
        print("APPROVAL ERROR:", exc)
        traceback.print_exc()
        return JSONResponse(
            status_code=500,
            content={
                "success": False,
                "error": str(exc),
            },
        )


# =========================
# Voice input (works for guests too, same as /api/travel)
# =========================

@app.post("/api/voice/transcribe")
async def voice_transcribe(audio: UploadFile = File(...)):
    try:
        audio_bytes = await audio.read()
        text = transcribe_audio(audio_bytes, filename=audio.filename or "audio.webm")
        return JSONResponse(content={"success": True, "text": text})
    except Exception as e:
        print("VOICE ERROR:", e)
        traceback.print_exc()
        return JSONResponse(
            status_code=500,
            content={"success": False, "error": str(e)}
        )


@app.get("/health")
async def health_check():
    return {
        "status": "ok",
        "message": "TripCrew API is running",
        "features": [
            "supervisor_agent",
            "input_guardrail",
            "human_in_the_loop",
            "mcp_tools",
            "auth",
            "voice_input",
        ],
    }


@app.get("/favicon.ico")
async def favicon():
    return JSONResponse(content={})


if __name__ == "__main__":
    uvicorn.run(
        "app:app",
        host="127.0.0.1",
        port=8000,
        reload=True
    )