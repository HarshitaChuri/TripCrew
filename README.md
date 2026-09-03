🧳 TripCrew — A Multi-Agent Travel Planner with LangGraph
==========================================================

An open-source AI travel planner that turns a natural-language trip request into a
practical travel plan with flight suggestions, hotel ideas, and a day-by-day itinerary.
Built with a multi-agent workflow using LangGraph, LangChain, and FastAPI — now with
optional sign-in and voice input.

Why this project?
------------------
Planning a trip usually means jumping between multiple websites, tools, and spreadsheets.
This project brings that flow into one experience by combining a *crew* of agents:

* a flight-search agent
* a hotel-research agent
* an itinerary-planning agent
* a final response agent

all coordinated through a LangGraph workflow.

Features
--------
* ✈️ Flight research using AviationStack
* 🏨 Hotel suggestions using Tavily search
* 🧠 Multi-agent orchestration with LangGraph
* 📝 Structured travel itinerary generation
* 🔐 Optional sign-in — plan as a guest, or create an account to save your trip history
* 🎙️ Voice input — speak your trip request instead of typing (transcribed via Groq Whisper)
* 🌐 FastAPI backend with a simple web interface
* 💾 Conversation state persistence using PostgreSQL
* ⚡ LLM-powered responses with Groq

Tech Stack
----------
* Python 3.10+
* FastAPI
* Jinja2 + HTML/CSS/JavaScript frontend
* LangGraph
* LangChain
* Groq LLMs (chat + Whisper transcription)
* PostgreSQL
* Tavily API
* AviationStack API
* JWT auth (python-jose + passlib)

Project Structure
------------------
```
.
├── app.py                # FastAPI app entry point (routes, auth wiring, voice endpoint)
├── backend.py             # LangGraph travel workflow
├── auth.py                 # JWT auth: register/login, password hashing, user dependencies
├── database.py            # Users + trips tables (psycopg)
├── requirements.txt
├── Dockerfile
├── .env.example
├── static/
│   ├── style.css
│   ├── script.js           # core plan-generation flow
│   ├── auth.js               # login/signup modal, trips panel
│   └── voice.js              # mic recording + transcription
├── templates/
│   └── index.html
└── tools/
    ├── flight_tool.py
    ├── tavily_tool.py
    └── voice_tool.py         # Groq Whisper transcription
```

Prerequisites
-------------
* Python 3.10 or newer
* PostgreSQL running and accessible
* API keys for: Groq, Tavily, AviationStack

Environment Variables
----------------------
Copy `.env.example` to `.env` and fill in your values:

```
DATABASE_URL=postgresql://user:password@localhost:5432/tripcrew_db
GROQ_API_KEY=your_groq_api_key
AVIATIONSTACK_API_KEY=your_aviationstack_api_key
TAVILY_API_KEY=your_tavily_api_key
DEFAULT_ORIGIN_IATA=DAC
JWT_SECRET_KEY=change_this_to_a_long_random_string
JWT_ALGORITHM=HS256
JWT_EXPIRE_MINUTES=10080
```

Generate a strong `JWT_SECRET_KEY` with:
```
openssl rand -hex 32
```

Installation
------------
```
python -m venv .venv
source .venv/bin/activate   # On Windows: .venv\Scripts\activate
pip install -r requirements.txt
```

Running the App
----------------
```
python app.py
```

Then open your browser at `http://127.0.0.1:8000/`.

On first run, `database.init_db()` automatically creates the `users` and `trips`
tables in your PostgreSQL database — no manual migration needed.

API Endpoints
-------------
* `GET /health` — health check
* `POST /api/travel` — submit a travel request (works for guests; saves to history if logged in)
* `POST /api/auth/register` — create an account
* `POST /api/auth/login` — sign in, returns a JWT
* `GET /api/auth/me` — current user (requires auth)
* `GET /api/trips` — list your saved trips (requires auth)
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
2. The flight agent gathers flight-related information.
3. The hotel agent searches for accommodation suggestions.
4. The itinerary agent creates a practical travel plan.
5. The final agent formats the result into a polished response.
6. If the user is signed in, the trip is saved to their history automatically.

Auth Design Notes
-------------------
* Guest mode is fully supported — `/api/travel` works with or without a JWT.
* When a valid token is present, the generated trip is linked to that user's account
  via the `trips` table (`user_id` → `thread_id`), so past conversations can be resumed
  from the "My Trips" panel.
* Passwords are hashed with bcrypt (via passlib); tokens are signed JWTs with a
  configurable expiry (`JWT_EXPIRE_MINUTES`, default 7 days).

Voice Design Notes
--------------------
* Recording happens in-browser via the `MediaRecorder` API (`static/voice.js`).
* The recorded clip is uploaded to `/api/voice/transcribe`, which calls Groq's
  Whisper endpoint (`tools/voice_tool.py`) and returns plain text.
* The transcript is inserted into the same textarea used for typed input, so it
  flows through the exact same `/api/travel` request — no special-casing needed
  in the LangGraph workflow itself.

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
intended as a practical example of combining LangGraph agents with real-world applications.
