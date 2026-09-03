from pathlib import Path
import traceback
from typing import Optional

import uvicorn
from fastapi import FastAPI, Request, Depends, UploadFile, File
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel

import database
import auth
from backend import run_travel_agent
from tools.voice_tool import transcribe_audio

BASE_DIR = Path(__file__).resolve().parent

app = FastAPI(
    title="TripCrew",
    description="A Multi-Agent Travel Planner with LangGraph",
    version="1.0.0"
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


# =========================
# Travel planner (guest-friendly: works with or without a token)
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

        # Only logged-in users get their trips saved to history.
        # Guests still get a full plan, it just isn't persisted to an account.
        if current_user:
            title = user_message[:80]
            database.save_trip(current_user.id, result["thread_id"], title)

        return JSONResponse(
            content={
                "success": True,
                "thread_id": result["thread_id"],
                "answer": result["answer"],
                "flight_results": result["flight_results"],
                "hotel_results": result["hotel_results"],
                "itinerary": result["itinerary"],
                "llm_calls": result["llm_calls"],
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
        "message": "TripCrew API is running"
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
