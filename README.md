🧳 TripCrew — A Multi-Agent Travel Planner with LangGraph, MCP, Supervisor, Guardrails & HITL
================================================================================================

An open-source AI travel planner that turns a natural-language trip request into a
practical travel plan with flight suggestions, hotel ideas, live weather, a budget
analysis, and a day-by-day itinerary — reviewed by you before it's finalized.
Built with a multi-agent workflow using LangGraph, the Model Context Protocol (MCP),
LangChain, and FastAPI.

Why this project?
------------------
Planning a trip usually means jumping between multiple websites, tools, and spreadsheets.
This project brings that flow into one experience, coordinated by a *crew* of agents
supervised by a routing layer that decides what's actually needed for each request:

* an input **guardrail** that filters out non-travel or harmful requests
* a **supervisor** that classifies the request and dynamically picks which specialists to run
* a **flight agent** (via the AviationStack MCP server)
* a **hotel agent** (via the Tavily MCP server)
* a **weather agent** (via a custom local MCP server, OpenWeather-backed)
* a **budget agent**
* an **itinerary agent** that drafts the full plan
* a **human-in-the-loop approval step** — you review the draft and approve or request changes
* a **final response agent** that polishes the approved (or revised) plan

all coordinated through a LangGraph state machine with PostgreSQL-backed persistence.

Features
--------
* 🧠 **Supervisor agent** — classifies each request and dynamically selects only the
  specialist agents actually needed (a simple "what's the weather in Tokyo?" skips the
  full itinerary pipeline entirely; "plan a 5 day trip to Dubai" runs the full crew)
* 🛡️ **Input guardrails** — an LLM-backed check blocks non-travel or harmful requests
  before any agent runs, with a graceful fail-open fallback if the check itself errors
* 🙋 **Human-in-the-loop (HITL)** — trip drafts pause for your review; approve to finalize,
  or send feedback to trigger a real revision round (capped at 3 rounds to prevent
  infinite loops), not just a single best-effort rewrite
* 🔌 **MCP-based tool access** — flights (AviationStack), hotels (Tavily), and weather
  (a custom local MCP server) are all accessed as MCP tools rather than direct API calls
* ✈️ Live flight data via the AviationStack MCP server
* 🏨 Hotel suggestions via the Tavily MCP server
* 🌤️ Live current weather + forecast via a custom OpenWeather-backed MCP server
* 💰 Budget feasibility analysis
* 📝 Structured, strictly-formatted travel itinerary generation (7 fixed sections)
* 🔐 Optional sign-in — plan as a guest, or create an account to save your trip history
  (a trip is only saved once it's actually approved/finalized, not while still a draft)
* 🎙️ Voice input — speak your trip request instead of typing (transcribed via Groq Whisper)
* 🌐 FastAPI backend with a simple web interface
* 💾 Conversation + approval-state persistence using PostgreSQL (LangGraph checkpointer)
* ⚡ LLM-powered responses with Groq, with automatic retry-with-backoff on rate limits

Tech Stack
----------
* Python 3.10+
* FastAPI
* Jinja2 + HTML/CSS/JavaScript frontend
* LangGraph (StateGraph, conditional routing, `interrupt()`/`Command` for HITL)
* LangChain
* MCP (Model Context Protocol) — `langchain-mcp-adapters`, `mcp`
* Groq LLMs (chat, reasoning models, + Whisper transcription)
* PostgreSQL
* Tavily MCP (remote)
* AviationStack MCP (local subprocess via `uvx`)
* Custom Weather MCP server (local subprocess, OpenWeather-backed)
* JWT auth (python-jose + passlib)
* Docker (with `uv`/`uvx` installed for the AviationStack MCP subprocess)

Project Structure
------------------
```
.
├── app.py                        # FastAPI app: auth, voice, /api/travel, /api/travel/approve
├── backend.py                     # LangGraph workflow: Supervisor, Guardrails, agents, HITL
├── mcp_client.py                  # MCP client: connects to Tavily, AviationStack, Weather servers
├── custom_weather_mcp_server.py   # Local MCP server exposing weather + forecast tools
├── auth.py                        # JWT auth: register/login, password hashing, user dependencies
├── database.py                    # Users + trips tables (psycopg)
├── requirements.txt
├── Dockerfile                     # Includes uv/uvx install for the AviationStack MCP subprocess
├── .env.example
├── static/
│   ├── style.css
│   ├── script.js                  # core flow: send, approve/reject, new trip, stop, read aloud
│   ├── auth.js                    # login/signup modal, trips panel
│   └── voice.js                   # mic recording + transcription
├── templates/
│   └── index.html                 # includes the HITL approval panel
└── tools/
    └── voice_tool.py               # Groq Whisper transcription
```

Note: `tools/flight_tool.py` and `tools/tavily_tool.py` from earlier versions are no
longer used — flight and hotel data now flow through MCP instead of direct API calls.

Prerequisites
-------------
* Python 3.10 or newer
* PostgreSQL running and accessible
* [uv](https://docs.astral.sh/uv/) installed locally (`pip install uv`) — needed to run
  the AviationStack MCP server via `uvx`
* API keys for: Groq, Tavily, AviationStack, OpenWeather

Environment Variables
----------------------
Copy `.env.example` to `.env` and fill in your values:

```
DATABASE_URL=postgresql://user:password@localhost:5432/tripcrew_db
GROQ_API_KEY=your_groq_api_key
GROQ_MODEL_NAME=openai/gpt-oss-120b
AVIATIONSTACK_API_KEY=your_aviationstack_api_key
TAVILY_API_KEY=your_tavily_api_key
OPENWEATHER_API_KEY=your_openweather_api_key
DEFAULT_ORIGIN_IATA=DAC
JWT_SECRET_KEY=change_this_to_a_long_random_string
JWT_ALGORITHM=HS256
JWT_EXPIRE_MINUTES=10080
```

Generate a strong `JWT_SECRET_KEY` with:
```
openssl rand -hex 32
```
(On Windows without `openssl`: `python -c "import secrets; print(secrets.token_hex(32))"`)

Installation
------------
```
python -m venv .venv
source .venv/bin/activate   # On Windows: .venv\Scripts\Activate.ps1
pip install -r requirements.txt
pip install uv               # provides uvx, used to run the AviationStack MCP server
```

Running the App
----------------
```
python app.py
```

Then open your browser at `http://127.0.0.1:8000/`.

On first run, `database.init_db()` automatically creates the `users` and `trips`
tables in your PostgreSQL database — no manual migration needed. LangGraph's
`PostgresSaver` checkpointer also sets up its own tables automatically.

API Endpoints
-------------
* `GET /health` — health check
* `POST /api/travel` — submit a travel request (works for guests). Returns either a
  final answer directly (`quick_info` requests, or guardrail-blocked requests) or a
  draft awaiting approval (`requires_approval: true`, `full_itinerary` requests)
* `POST /api/travel/approve` — approve or request revisions on a paused draft.
  `{"thread_id": "...", "approved": true}` to finalize, or
  `{"thread_id": "...", "approved": false, "feedback": "..."}` to request a revision.
  A trip is only saved to a logged-in user's history once this call finalizes it.
* `POST /api/auth/register` — create an account
* `POST /api/auth/login` — sign in, returns a JWT
* `GET /api/auth/me` — current user (requires auth)
* `GET /api/trips` — list your saved trips (requires auth)
* `DELETE /api/trips/{trip_id}` — delete a saved trip (requires auth, scoped to the owner)
* `POST /api/voice/transcribe` — upload an audio clip, get back transcribed text

Example request:
```
curl -X POST http://127.0.0.1:8000/api/travel \
  -H "Content-Type: application/json" \
  -d '{"message":"Plan a 3-day trip to Tokyo with a budget of $1200"}'
```

How the Workflow Works
------------------------
1. The user submits a travel request (typed or spoken).
2. **Guardrail check** — an LLM call determines whether the request is a legitimate
   travel-related ask. If not, a polite explanation is returned immediately and nothing
   else runs.
3. **Supervisor** — classifies the request as `quick_info` (a standalone question) or
   `full_itinerary` (an actual trip-planning request), and picks which specialist
   agents are actually needed.
4. **Specialist agents run** (only the ones selected) — flight, hotel, weather, and/or
   budget — each via its own MCP tool or direct LLM call.
5. **Routing splits here:**
   - `quick_info` → a lightweight **quick answer agent** responds directly and the
     request ends. No draft, no approval step.
   - `full_itinerary` → the **itinerary agent** drafts a full day-by-day plan, then the
     graph **pauses** (`requires_approval: true`) waiting for human review.
6. **Human-in-the-loop** — the user approves the draft, or sends feedback. Feedback
   loops back to the itinerary agent for a real revision (up to `MAX_REVISIONS = 3`
   rounds) and pauses for approval again each time.
7. **Final agent** — once approved (or the revision cap is hit), produces the polished,
   strictly 7-section final response.
8. If the user is signed in, the trip is saved to their history **only at this final step**.

Auth Design Notes
-------------------
* Guest mode is fully supported — `/api/travel` and `/api/travel/approve` both work
  with or without a JWT.
* A trip is saved to a user's history only once it's genuinely finalized (not while a
  draft is still pending approval or mid-revision) — via `database.save_trip()` in the
  `/api/travel/approve` handler.
* Passwords are hashed with bcrypt (via passlib); tokens are signed JWTs with a
  configurable expiry (`JWT_EXPIRE_MINUTES`, default 7 days).

MCP Design Notes
-------------------
* **Tavily** is accessed as a *remote* MCP server (`mcp.tavily.com`), not the
  `tavily-python` SDK directly.
* **AviationStack** runs as a *local subprocess* MCP server, launched via
  `uvx --with "mcp<2" aviationstack-mcp`. The `mcp<2` pin matters: this package still
  uses the pre-2.0 MCP SDK API (`FastMCP`, renamed to `MCPServer` in `mcp` 2.x) —
  without pinning it, `uvx` installs the latest `mcp` by default and the subprocess
  crashes on import.
* **Weather** runs as a local subprocess too (`custom_weather_mcp_server.py`), launched
  with the same Python interpreter running the main app.
* Each MCP tool call is wrapped in a `try/except` per specialist agent, so one broken
  MCP server degrades gracefully (falls back to general LLM guidance) rather than
  crashing the whole request.

Reliability Notes (Groq free-tier specific)
-----------------------------------------------
This pipeline makes significantly more LLM calls per request than a simple single-agent
setup, which surfaces a few free-tier constraints worth knowing about if you fork this:

* **Rate limits (429)** — `_invoke_with_retry()` in `backend.py` catches Groq's
  `RateLimitError`, parses the suggested wait time from the error message, and retries
  automatically (up to 3 attempts) rather than failing the whole request.
* **Request-too-large (413)** — passing full, untruncated agent output into later
  prompts (itinerary/final agents both reference flight/hotel/weather/budget results)
  can exceed the free tier's per-minute token budget in a single call, which retrying
  can't fix. `_truncate()` caps how much prior-agent output gets re-embedded downstream.
* **Silent output truncation** — `gpt-oss-120b` is a reasoning model that spends part of
  its token budget on hidden internal reasoning before writing the visible answer.
  Without an explicit `max_tokens`, long structured responses (a 7-section itinerary
  with tables) can get cut off mid-way with no error. `llm_long_form` (used only by
  `itinerary_agent` and `final_agent`) sets `max_tokens=8000` and
  `reasoning_effort="medium"` to avoid this while still following the strict formatting
  instructions.

Voice Design Notes
--------------------
* Recording happens in-browser via the `MediaRecorder` API (`static/voice.js`), with a
  minimum recording-length check client-side to avoid sending near-empty clips.
* The recorded clip is uploaded to `/api/voice/transcribe`, which calls Groq's Whisper
  endpoint (`tools/voice_tool.py`) and returns plain text.
* Known Whisper failure mode: silent/near-silent audio can produce hallucinated
  generic phrases (e.g. "Thank you.", "Thanks for watching!") — an artifact of being
  trained partly on YouTube captions. `tools/voice_tool.py` detects and rejects these
  known patterns rather than passing them through as if the user actually said them.

A Note on Groq Model Names
-----------------------------
Groq periodically retires older models (e.g. `llama-3.3-70b-versatile` was
decommissioned in August 2026). The LLM used throughout `backend.py` is controlled by
`GROQ_MODEL_NAME` in `.env` rather than hardcoded, so if you ever see a
`model_not_found` error, check the current model list at console.groq.com/docs/models
and update `.env` — no code changes needed.

Deployment (Render)
-----------------------
* The `Dockerfile` installs `uv`/`uvx` (via `COPY --from=ghcr.io/astral-sh/uv:latest`)
  in addition to the Python dependencies — required for the AviationStack MCP
  subprocess to run in the container. Without this, flight-related requests will fail
  in production even though everything else works.
* The first flight-related request after a fresh deploy will be slightly slower than
  subsequent ones, since `uvx` downloads `aviationstack-mcp` and its dependencies on
  first invocation inside the container.
* Recommended workflow: keep a separate "staging" Render Web Service pointed at your
  current feature branch (switchable via Settings → Branch) for testing Docker/MCP
  behavior before merging to `main`, since local Docker testing may not always be
  available. Production stays pointed at `main`.

Contributing
------------
Contributions are welcome. If you want to improve the app, add new travel features,
or fix issues:

1. Fork the repository
2. Create a feature branch
3. Make your changes
4. Open a pull request

Acknowledgments
----------------
This project is built with the help of modern LLM tooling and travel APIs, and it is
intended as a practical example of combining LangGraph agents, MCP tools, and
human-in-the-loop review patterns in a real-world application.