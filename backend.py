import os
import certifi
from dotenv import load_dotenv

load_dotenv()
os.environ["SSL_CERT_FILE"] = certifi.where()
os.environ["REQUESTS_CA_BUNDLE"] = certifi.where()

from typing import Any, TypedDict, Annotated
import operator
import uuid
import asyncio
import json
import psycopg
from psycopg.rows import dict_row
from langgraph.graph import StateGraph, START, END
from langgraph.checkpoint.postgres import PostgresSaver
from langgraph.types import Command, interrupt
from langchain_core.messages import (
    AnyMessage,
    HumanMessage,
    AIMessage,
    SystemMessage,
)
from langchain_groq import ChatGroq
from groq import RateLimitError
import re
import time


from mcp_client import (
    tavily_mcp_search,
    aviation_mcp_call,
    extract_destination,
    forecast_mcp_search,
    weather_mcp_search,
)


def get_database_url():
    database_url = os.getenv("DATABASE_URL")

    if not database_url:
        raise ValueError(
            "DATABASE_URL is missing. "
            "Please add your PostgreSQL connection string to .env"
        )

    if "sslmode=" not in database_url:
        separator = "&" if "?" in database_url else "?"
        database_url = f"{database_url}{separator}sslmode=require"

    return database_url


GROQ_API_KEY = os.getenv("GROQ_API_KEY")

if not GROQ_API_KEY:
    raise ValueError("GROQ_API_KEY is missing. Please add it to your .env file.")

# =========================
# LLM — model name is configurable via .env, not hardcoded, so a future
# Groq model deprecation only needs a .env change (see GROQ_MODEL_NAME).
# =========================
GROQ_MODEL_NAME = os.getenv("GROQ_MODEL_NAME", "openai/gpt-oss-120b")

llm = ChatGroq(
    model=GROQ_MODEL_NAME,
    api_key=GROQ_API_KEY,
)

# Separate instance with an explicit, higher max_tokens for the two agents
# that generate long, multi-section formatted content (itinerary_agent,
# final_agent). Without an explicit max_tokens, ChatGroq falls back to
# Groq's own default output cap, which isn't generous enough for a full
# 7-section itinerary with several tables — the model just stops mid-way
# (finish_reason: "length") with no error, producing a silently truncated
# response. Other agents (guardrail/supervisor/budget/quick_answer) stay
# on the smaller default `llm` since they don't need long output and this
# conserves the shared 8,000 TPM free-tier budget.
llm_long_form = ChatGroq(
    model=GROQ_MODEL_NAME,
    api_key=GROQ_API_KEY,
    max_tokens=8000,
    
    # gpt-oss-120b is a "reasoning" model — it spends part of max_tokens on
    # hidden internal reasoning before writing the visible answer. "low"
    # keeps that internal overhead small, leaving more of the budget for
    # the actual formatted itinerary text instead of invisible thinking.
    reasoning_effort="medium",
    )

# Safety cap on the human-in-the-loop revision cycle. Without this, a user
# rejecting a draft repeatedly could loop the graph forever, burning LLM
# and MCP calls with no exit. After this many rejected rounds, the last
# feedback given is applied one final time and the plan is finalized
# regardless, rather than looping indefinitely.
MAX_REVISIONS = 3

# =========================
# State - original fields kept, new control fields added
# =========================
class TravelState(TypedDict, total=False):
    messages: Annotated[list[AnyMessage], operator.add]
    user_query: str

    # Supervisor + guardrail state
    guardrail_allowed: bool
    guardrail_reason: str
    request_type: str  # "quick_info" or "full_itinerary"
    selected_agents: list[str]
    trip_constraints: dict[str, Any]
    supervisor_reasoning: str

    # Original specialist results
    flight_results: str
    hotel_results: str
    weather_results: str
    itinerary: str

    # New budget + HITL state
    budget_results: str
    approval_request: str
    approved: bool
    human_feedback: str
    revision_count: int
    final_response: str

    llm_calls: int


# =========================
# Shared helpers
# =========================
KNOWN_AGENTS = {
    "flight_agent",
    "hotel_agent",
    "weather_agent",
    "budget_agent",
    "itinerary_agent",
}

AGENT_ORDER = [
    "flight_agent",
    "hotel_agent",
    "weather_agent",
    "budget_agent",
    "itinerary_agent",
]


def _invoke_with_retry(messages, max_retries: int = 3, llm_client=None):
    """
    Wraps llm.invoke() with retry-on-rate-limit logic.

    This pipeline makes several more LLM calls per request than the
    original 4-agent version (guardrail + supervisor + up to 4 specialists
    + itinerary + final = up to 7), so it's much more likely to hit Groq's
    free-tier TPM (tokens-per-minute) limit mid-run. Without this, a single
    429 would crash the whole graph run and lose the user's request.
    Groq's error message includes a suggested wait time ("try again in
    10.4s") — we parse that when available, otherwise fall back to a
    fixed 15s wait.
    """
    client = llm_client or llm
    last_exc = None

    for attempt in range(max_retries):
        try:
            return client.invoke(messages)
        except RateLimitError as exc:
            last_exc = exc
            wait_seconds = 15.0
            match = re.search(r"try again in ([\d.]+)s", str(exc))
            if match:
                wait_seconds = float(match.group(1)) + 1.0

            print(
                f"Rate limited by Groq (attempt {attempt + 1}/{max_retries}). "
                f"Waiting {wait_seconds:.1f}s before retrying...",
                flush=True,
            )
            time.sleep(wait_seconds)

    # All retries exhausted — raise the last real error rather than
    # swallowing it, so it surfaces clearly in app.py's error handling.
    raise last_exc


def _llm_text(system_prompt: str, user_prompt: str) -> str:
    response = _invoke_with_retry(
        [
            SystemMessage(content=system_prompt),
            HumanMessage(content=user_prompt),
        ]
    )
    return str(response.content)


def _json_from_llm(text: str) -> dict[str, Any]:
    """Extract the first complete JSON object returned by the model."""
    start = text.find("{")
    end = text.rfind("}")

    if start == -1 or end == -1 or end < start:
        raise ValueError("The model did not return a JSON object.")

    return json.loads(text[start : end + 1])


def _empty_constraints() -> dict[str, Any]:
    return {
        "destination": "",
        "origin": "",
        "duration": "",
        "budget": "",
        "travel_style": "",
        "special_preferences": [],
    }


def _truncate(text: str, max_chars: int = 600) -> str:
    """
    Truncates prior-agent output before re-embedding it into a later
    prompt. itinerary_agent and final_agent both concatenate flight +
    hotel + weather + budget results, and final_agent adds the full draft
    itinerary on top of that — without this, the same content compounds
    across 3-4 prompts in the same request and can exceed Groq's free-tier
    per-minute token budget in a single call (HTTP 413), which retrying
    can't fix since the oversized request is the same size every time.
    """
    text = text or ""
    if len(text) <= max_chars:
        return text
    return text[:max_chars].rsplit(" ", 1)[0] + "... [truncated for length]"


# =========================
# Supervisor Agent + Input Guardrail
# =========================
def supervisor_agent(state: TravelState):
    query = state["user_query"]
    llm_calls = state.get("llm_calls", 0)

    guardrail_prompt = f"""
Determine whether the following request belongs to travel planning or travel
information. Valid requests can include destinations, flights, hotels, weather,
budgets, visas, transportation, sightseeing, food, packing, or itineraries.

Block clearly unrelated requests and requests asking for harmful or illegal
instructions. Do not block a valid travel request merely because some details
are missing.

Return strict JSON only:
{{
  "allowed": true,
  "reason": ""
}}

User request:
{query}
"""

    # Fail open on parser/model errors so a temporary JSON-format issue does not
    # break the original travel-planning behavior.
    try:
        guardrail_raw = _llm_text(
            "You are the input guardrail for a travel-planning application. "
            "Return strict JSON only.",
            guardrail_prompt,
        )
        guardrail_result = _json_from_llm(guardrail_raw)
        allowed = bool(guardrail_result.get("allowed", True))
        guardrail_reason = str(guardrail_result.get("reason", "")).strip()
        llm_calls += 1
    except Exception as exc:
        print(f"Guardrail fallback used: {exc}")
        allowed = True
        guardrail_reason = "Guardrail validation fallback allowed the request."

    if not allowed:
        reason = guardrail_reason or (
            "TripCrew can only help with travel-planning requests. "
            "Please ask about a destination, flight, hotel, weather, budget, "
            "or itinerary."
        )
        return {
            "guardrail_allowed": False,
            "guardrail_reason": reason,
            "selected_agents": [],
            "trip_constraints": _empty_constraints(),
            "supervisor_reasoning": reason,
            "final_response": reason,
            "messages": [AIMessage(content=f"Guardrail blocked request: {reason}")],
            "llm_calls": llm_calls,
        }

    supervisor_prompt = f"""
You are the supervisor of a multi-agent travel-planning system.
Choose only the specialist agents needed for the request.

Available agents:
- flight_agent: flights, airports, airlines, routes, airfare, or booking advice
- hotel_agent: hotels, accommodation, neighborhoods, or places to stay
- weather_agent: weather, climate, season, forecast, or packing advice
- budget_agent: cost, affordability, price limits, or budget feasibility
- itinerary_agent: creates a full multi-day integrated travel plan — only needed
  for actual trip-planning requests, never for a single standalone question

First classify the request type:
- "quick_info": the user is asking a specific standalone question (e.g. "what's
  the weather in Tokyo", "flights from Mumbai to Dubai", "is Bali affordable on
  a $500 budget") and does NOT want a multi-day itinerary or a review step.
  Select only the specialist agent(s) actually needed to answer it, and do
  NOT include itinerary_agent.
- "full_itinerary": the user wants an actual trip plan (e.g. "plan a 4 day trip
  to Dubai", "create an itinerary for Japan"). Always include itinerary_agent
  for this type, plus whichever specialists are relevant.

Return strict JSON only using this schema:
{{
  "request_type": "quick_info",
  "selected_agents": ["flight_agent", "hotel_agent", "weather_agent", "budget_agent", "itinerary_agent"],
  "trip_constraints": {{
    "destination": "",
    "origin": "",
    "duration": "",
    "budget": "",
    "travel_style": "",
    "special_preferences": []
  }},
  "reasoning": ""
}}

User request:
{query}
"""

    try:
        supervisor_raw = _llm_text(
            "You route work to travel specialist agents. Return strict JSON only.",
            supervisor_prompt,
        )
        parsed = _json_from_llm(supervisor_raw)

        request_type = str(parsed.get("request_type", "full_itinerary")).strip()
        if request_type not in ("quick_info", "full_itinerary"):
            request_type = "full_itinerary"

        requested_agents = parsed.get("selected_agents", [])
        selected_agents = [
            name for name in AGENT_ORDER
            if name in requested_agents and name in KNOWN_AGENTS
        ]

        # Only full trip-planning requests get the itinerary + HITL approval
        # flow forced on. A quick factual question shouldn't be turned into
        # a draft itinerary the user has to review and approve.
        if request_type == "full_itinerary" and "itinerary_agent" not in selected_agents:
            selected_agents.append("itinerary_agent")
        elif request_type == "quick_info":
            selected_agents = [a for a in selected_agents if a != "itinerary_agent"]
            if not selected_agents:
                # Guardrail already confirmed this is a valid travel question —
                # fall back to hotel_agent (general web search) so there's at
                # least something to answer with.
                selected_agents = ["hotel_agent"]

        constraints = _empty_constraints()
        parsed_constraints = parsed.get("trip_constraints", {})
        if isinstance(parsed_constraints, dict):
            constraints.update(parsed_constraints)

        reasoning = str(parsed.get("reasoning", "")).strip()
        llm_calls += 1
    except Exception as exc:
        print(f"Supervisor fallback used: {exc}")
        # Original workflow behavior is preserved as the fallback.
        request_type = "full_itinerary"
        selected_agents = AGENT_ORDER.copy()
        constraints = _empty_constraints()
        reasoning = (
            "Supervisor parsing failed, so the original full travel workflow "
            "was selected as a safe fallback."
        )

    return {
        "guardrail_allowed": True,
        "guardrail_reason": guardrail_reason,
        "request_type": request_type,
        "selected_agents": selected_agents,
        "trip_constraints": constraints,
        "supervisor_reasoning": reasoning,
        "messages": [AIMessage(content="Supervisor created the agent plan.")],
        "llm_calls": llm_calls,
    }


# =========================
# Guardrail blocked response
# =========================
def guardrail_blocked_agent(state: TravelState):
    reason = state.get("final_response") or state.get("guardrail_reason") or (
        "This request was blocked by the travel input guardrail."
    )
    return {
        "final_response": reason,
        "messages": [AIMessage(content=reason)],
    }


# =========================
# Flight Agent - now via AviationStack MCP
# =========================
FLIGHT_AGENT_PROMPT = """
You are a travel flight expert.

User Query:
{query}

Airport Information:
{airport_data}

Airline Information:
{airline_data}

Generate:
1. Likely departure airport
2. Likely arrival airport
3. Airlines serving this route
4. Typical flight duration
5. Estimated airfare range
6. Peak season pricing warning
7. Booking advice

Return concise travel guidance.
"""


def flight_agent(state: TravelState):
    query = state["user_query"]

    try:
        airports = asyncio.run(aviation_mcp_call("list_airports"))
        airlines = asyncio.run(aviation_mcp_call("list_airlines"))

        prompt = FLIGHT_AGENT_PROMPT.format(
            query=query,
            # Reduced from 3000 to 1500 chars each — this pipeline makes
            # far more LLM calls per request than before, so trimming the
            # biggest single payload helps stay under Groq's free-tier
            # TPM limit.
            airport_data=str(airports)[:1500],
            airline_data=str(airlines)[:1500],
        )

        response = _invoke_with_retry(
            [
                SystemMessage(content="You are an expert travel flight planner."),
                HumanMessage(content=prompt),
            ]
        )
        flight_data = response.content
    except Exception as exc:
        print(f"FLIGHT AGENT MCP ERROR: {type(exc).__name__}: {exc}", flush=True)
        flight_data = f"Flight information unavailable: {exc}"

    return {
        "flight_results": flight_data,
        "messages": [AIMessage(content="Flight recommendations generated")],
        "llm_calls": state.get("llm_calls", 0) + 1,
    }


# =========================
# Hotel Agent - now via Tavily MCP
# =========================
def hotel_agent(state: TravelState):
    query = f"Best hotels for {state['user_query']}"

    try:
        hotel_results = asyncio.run(tavily_mcp_search(query))
    except Exception as exc:
        print(f"HOTEL AGENT MCP ERROR: {type(exc).__name__}: {exc}", flush=True)
        hotel_results = (
            "Live hotel search is temporarily unavailable. "
            "Provide general accommodation and neighborhood "
            "guidance based on the destination and clearly "
            "label it as non-live advice."
        )

    return {
        "hotel_results": hotel_results,
        "messages": [AIMessage(content="Hotel information processed.")],
        "llm_calls": state.get("llm_calls", 0) + 1,
    }


# =========================
# Weather Agent - new specialist, via custom Weather MCP
# =========================
def weather_agent(state: TravelState):
    city = "the destination"  # fallback label if extraction itself fails

    try:
        city = extract_destination(state["user_query"])
        weather_data = asyncio.run(weather_mcp_search(city))
        forecast_data = asyncio.run(forecast_mcp_search(city))

        weather_results = f"""
Current Weather:
{weather_data}

Forecast:
{forecast_data}
"""
    except Exception as exc:
        print(f"WEATHER AGENT MCP ERROR: {type(exc).__name__}: {exc}", flush=True)
        weather_results = (
            f"Live weather information for {city} "
            "is temporarily unavailable. Give general "
            "seasonal guidance and advise the traveler "
            "to verify the forecast before departure."
        )

    return {
        "weather_results": weather_results,
        "messages": [AIMessage(content="Weather information processed.")],
    }


# =========================
# Budget Agent - new specialist
# =========================
def budget_agent(state: TravelState):
    prompt = f"""
Analyze whether this trip is realistic for the user's budget.

User Query:
{state['user_query']}

Trip Constraints:
{state.get('trip_constraints', {})}

Flight Results:
{_truncate(state.get('flight_results', ''))}

Hotel Results:
{_truncate(state.get('hotel_results', ''))}

Weather Results:
{_truncate(state.get('weather_results', ''))}

Return:
1. Estimated cost categories
2. Budget risk areas
3. Money-saving suggestions
4. Overall feasibility

If exact live prices are unavailable, clearly label estimates as approximate.
"""

    response = _invoke_with_retry(
        [
            SystemMessage(content="You are a practical travel budget analyst."),
            HumanMessage(content=prompt),
        ]
    )

    return {
        "budget_results": response.content,
        "messages": [AIMessage(content="Budget assessment generated.")],
        "llm_calls": state.get("llm_calls", 0) + 1,
    }


# =========================
# Quick Answer Agent - for standalone questions (e.g. "what's the weather
# in Tokyo") that don't need a multi-day itinerary or human approval.
# Goes straight to END, skipping itinerary_agent and human_approval.
# =========================
def quick_answer_agent(state: TravelState):
    prompt = f"""
Answer the user's specific travel-related question directly and concisely,
using the information gathered below. Do NOT create a multi-day itinerary
or trip plan, and do not use headers like "Trip Summary" or "Day-by-Day
Itinerary" — just answer what was actually asked, in a few short paragraphs.

User Question:
{state['user_query']}

Available Information:

Flight Results:
{_truncate(state.get('flight_results', ''), max_chars=800)}

Hotel/Search Results:
{_truncate(state.get('hotel_results', ''), max_chars=800)}

Weather Results:
{_truncate(state.get('weather_results', ''), max_chars=1200)}

Budget Results:
{_truncate(state.get('budget_results', ''), max_chars=800)}

If a piece of information above is empty, simply don't mention it — don't
say it's missing.
"""

    response = _invoke_with_retry(
        [
            SystemMessage(content="You are a helpful, concise travel information assistant."),
            HumanMessage(content=prompt),
        ]
    )

    return {
        "final_response": response.content,
        "messages": [response],
        "llm_calls": state.get("llm_calls", 0) + 1,
    }


# =========================
# Itinerary Agent - now incorporates human revision feedback on loop-back
# =========================
def itinerary_agent(state: TravelState):
    revision_count = state.get("revision_count", 0)
    human_feedback = state.get("human_feedback", "")

    revision_section = ""
    if revision_count > 0 and human_feedback:
        revision_section = f"""
This is a REVISION (round {revision_count} of {MAX_REVISIONS}).
The human reviewer requested these changes to the previous draft — apply them:
{human_feedback}

Previous Draft:
{_truncate(state.get('itinerary', ''), max_chars=1200)}
"""

    prompt = f"""
Create a complete travel itinerary.

User Query:
{state['user_query']}

Trip Constraints:
{state.get('trip_constraints', {})}

Flight Results:
{_truncate(state.get('flight_results', ''))}

Hotel Results:
{_truncate(state.get('hotel_results', ''))}

Weather Results:
{_truncate(state.get('weather_results', ''))}

Budget Results:
{_truncate(state.get('budget_results', ''))}
{revision_section}
Make the itinerary practical, budget-aware, and easy to follow. Keep each
day to 3-5 bullet points rather than an hour-by-hour schedule, and keep
tables compact.
Create a clear draft that is ready for human review.
"""

    response = _invoke_with_retry(
        [
            SystemMessage(content="You are an expert travel planner."),
            HumanMessage(content=prompt),
        ],
        llm_client=llm_long_form,
    )

    approval_request = (
        "Please review the generated draft itinerary. Approve it to create the "
        "final polished plan, or provide feedback for revision."
    )

    return {
        "itinerary": response.content,
        "approval_request": approval_request,
        "messages": [AIMessage(content="Draft itinerary created for human review.")],
        "llm_calls": state.get("llm_calls", 0) + 1,
    }


# =========================
# Human-in-the-Loop approval
# =========================
def human_approval_agent(state: TravelState):
    # Do not wrap interrupt() in try/except. LangGraph uses it to pause execution.
    review = interrupt(
        {
            "question": "Do you approve this itinerary?",
            "draft_itinerary": state.get("itinerary", ""),
            "approval_request": state.get("approval_request", ""),
            "selected_agents": state.get("selected_agents", []),
            "supervisor_reasoning": state.get("supervisor_reasoning", ""),
            "revision_count": state.get("revision_count", 0),
            "max_revisions": MAX_REVISIONS,
            "expected_response": {
                "approved": True,
                "feedback": "Optional revision feedback",
            },
        }
    )

    approved = bool(review.get("approved", False))
    human_feedback = str(review.get("feedback", "")).strip()
    revision_count = state.get("revision_count", 0)

    if not approved:
        revision_count += 1

    return {
        "approved": approved,
        "human_feedback": human_feedback,
        "revision_count": revision_count,
        "messages": [AIMessage(content="Human approval step completed.")],
    }


# =========================
# Final Response Agent - original format kept, HITL feedback added
# =========================
def final_agent(state: TravelState):
    revision_count = state.get("revision_count", 0)

    if state.get("approved", False):
        review_instruction = (
            "The user approved the draft. Preserve its decisions while polishing it."
        )
    elif revision_count >= MAX_REVISIONS:
        review_instruction = (
            f"The maximum number of revision rounds ({MAX_REVISIONS}) was reached. "
            f"Apply this last feedback as best as possible and finalize the plan: "
            f"{state.get('human_feedback', '') or 'No further feedback given.'}"
        )
    else:
        review_instruction = f"""
The user requested a revision. Apply this feedback carefully:
{state.get('human_feedback', '') or 'Improve the draft before finalizing it.'}
"""

    final_prompt = f"""
Generate the final travel response for the user.

Human Review:
{review_instruction}

User Request:
{state['user_query']}

Supervisor Constraints:
{state.get('trip_constraints', {})}

Flights:
{_truncate(state.get('flight_results', ''), max_chars=400)}

Hotels:
{_truncate(state.get('hotel_results', ''), max_chars=400)}

Weather:
{_truncate(state.get('weather_results', ''), max_chars=400)}

Budget Analysis:
{_truncate(state.get('budget_results', ''), max_chars=400)}

Draft Itinerary:
{_truncate(state.get('itinerary', ''), max_chars=2500)}

Format the final answer using these exact section headers, word-for-word,
each as its own markdown heading, in this exact order, and no others:

## 1. Trip Summary
## 2. Flight Information
## 3. Hotel Suggestions
## 4. Weather Information
## 5. Day-by-Day Itinerary
## 6. Estimated Budget
## 7. Final Recommendations

Do not rename these headers, do not merge them, and do not add any section
that isn't one of these 7 — for example, do not add a "Quick-Look Summary",
"Practical Tips", "Quick Reference", "Ready to Book", or "Packing
Checklist" section. If you have tips or practical advice, put them inside
"Final Recommendations" as bullet points, not as a separate heading.

Important:
- Use the 7 headers above verbatim, including the numbers.
- Be concise. For "Day-by-Day Itinerary", give 3-5 bullet points per day
  (not an hour-by-hour table with dual time zones) — a traveler wants the
  shape of the day, not a minute-by-minute schedule.
- Keep tables small — a handful of rows, not exhaustive listings.
- Be clear and practical.
- Mention that live flight APIs may not provide ticket prices when pricing is unavailable.
- Include weather-based travel advice.
- Keep the response useful for real travel planning.
- Incorporate the human feedback when revision was requested.
"""

    response = _invoke_with_retry(
        [
            SystemMessage(
                content="You are a professional AI travel booking assistant."
            ),
            HumanMessage(content=final_prompt),
        ],
        llm_client=llm_long_form,
    )

    return {
        "final_response": response.content,
        "messages": [response],
        "llm_calls": state.get("llm_calls", 0) + 1,
    }


# =========================
# Dynamic Supervisor Routing
# =========================
ROUTE_MAP = {
    "guardrail_blocked": "guardrail_blocked",
    "flight_agent": "flight_agent",
    "hotel_agent": "hotel_agent",
    "weather_agent": "weather_agent",
    "budget_agent": "budget_agent",
    "quick_answer_agent": "quick_answer_agent",
    "itinerary_agent": "itinerary_agent",
}


def _selected_agents(state: TravelState) -> list[str]:
    selected = state.get("selected_agents", [])
    return [agent for agent in AGENT_ORDER if agent in selected]


def _terminal_node(state: TravelState) -> str:
    """Where to go once all selected specialist agents have run.
    quick_info requests skip the itinerary + human-approval flow entirely."""
    if state.get("request_type") == "quick_info":
        return "quick_answer_agent"
    return "itinerary_agent"


def route_from_supervisor(state: TravelState) -> str:
    if not state.get("guardrail_allowed", True):
        return "guardrail_blocked"

    selected = _selected_agents(state)
    return selected[0] if selected else _terminal_node(state)


def route_after_agent(current_agent: str):
    def route(state: TravelState) -> str:
        selected = _selected_agents(state)
        current_index = AGENT_ORDER.index(current_agent)

        for next_agent in AGENT_ORDER[current_index + 1 :]:
            if next_agent in selected:
                return next_agent

        return _terminal_node(state)

    return route


def route_after_approval(state: TravelState) -> str:
    """
    This is the fix for the flowchart's 'if changes requested, loop back
    to relevant agents' step. The tutorial version always went straight
    to final_agent regardless of approval — this version actually loops
    back to itinerary_agent (capped by MAX_REVISIONS) so rejection means
    a real second draft + second approval round, not a single rewrite.
    """
    if state.get("approved", False):
        return "final_agent"

    if state.get("revision_count", 0) >= MAX_REVISIONS:
        return "final_agent"

    return "itinerary_agent"


# =========================
# Build Graph
# =========================
graph = StateGraph(TravelState)

graph.add_node("supervisor", supervisor_agent)
graph.add_node("guardrail_blocked", guardrail_blocked_agent)
graph.add_node("flight_agent", flight_agent)
graph.add_node("hotel_agent", hotel_agent)
graph.add_node("weather_agent", weather_agent)
graph.add_node("budget_agent", budget_agent)
graph.add_node("quick_answer_agent", quick_answer_agent)
graph.add_node("itinerary_agent", itinerary_agent)
graph.add_node("human_approval", human_approval_agent)
graph.add_node("final_agent", final_agent)

graph.add_edge(START, "supervisor")
graph.add_conditional_edges("supervisor", route_from_supervisor, ROUTE_MAP)

graph.add_conditional_edges(
    "flight_agent", route_after_agent("flight_agent"), ROUTE_MAP
)
graph.add_conditional_edges(
    "hotel_agent", route_after_agent("hotel_agent"), ROUTE_MAP
)
graph.add_conditional_edges(
    "weather_agent", route_after_agent("weather_agent"), ROUTE_MAP
)
graph.add_conditional_edges(
    "budget_agent", route_after_agent("budget_agent"), ROUTE_MAP
)

graph.add_edge("itinerary_agent", "human_approval")
graph.add_conditional_edges(
    "human_approval",
    route_after_approval,
    {"final_agent": "final_agent", "itinerary_agent": "itinerary_agent"},
)
graph.add_edge("final_agent", END)
graph.add_edge("guardrail_blocked", END)
graph.add_edge("quick_answer_agent", END)

# =========================
# PostgreSQL Checkpointer - original persistence kept
# =========================
DATABASE_URL = get_database_url()
_conn = psycopg.connect(
    DATABASE_URL,
    autocommit=True,
    row_factory=dict_row,
)
checkpointer = PostgresSaver(_conn)
checkpointer.setup()

travel_graph = graph.compile(checkpointer=checkpointer)


# =========================
# FastAPI-facing helpers
# =========================
def _interrupt_payload(result: dict[str, Any]) -> dict[str, Any] | None:
    interrupts = result.get("__interrupt__", [])
    if not interrupts:
        return None

    first_interrupt = interrupts[0]
    payload = getattr(first_interrupt, "value", first_interrupt)
    return payload if isinstance(payload, dict) else {"value": payload}


def _serialize_result(
    result: dict[str, Any],
    thread_id: str,
) -> dict[str, Any]:
    messages = result.get("messages", [])
    last_message = messages[-1].content if messages else ""
    answer = result.get("final_response") or last_message
    interrupt_payload = _interrupt_payload(result)

    if interrupt_payload:
        answer = interrupt_payload.get("draft_itinerary") or result.get(
            "itinerary", ""
        )

    return {
        "thread_id": thread_id,
        "user_query": result.get("user_query", ""),
        "answer": answer,
        "requires_approval": interrupt_payload is not None,
        "approval_request": (
            interrupt_payload.get("approval_request", "")
            if interrupt_payload
            else result.get("approval_request", "")
        ),
        "flight_results": result.get("flight_results", ""),
        "hotel_results": result.get("hotel_results", ""),
        "weather_results": result.get("weather_results", ""),
        "budget_results": result.get("budget_results", ""),
        "itinerary": (
            interrupt_payload.get("draft_itinerary", "")
            if interrupt_payload
            else result.get("itinerary", "")
        ),
        "selected_agents": result.get("selected_agents", []),
        "trip_constraints": result.get("trip_constraints", {}),
        "supervisor_reasoning": result.get("supervisor_reasoning", ""),
        "guardrail_allowed": result.get("guardrail_allowed", True),
        "guardrail_reason": result.get("guardrail_reason", ""),
        "request_type": result.get("request_type", "full_itinerary"),
        "approved": result.get("approved"),
        "human_feedback": result.get("human_feedback", ""),
        "revision_count": result.get("revision_count", 0),
        "max_revisions": MAX_REVISIONS,
        "llm_calls": result.get("llm_calls", 0),
    }


def run_travel_agent(user_input: str, thread_id: str | None = None):
    """Start a new travel-planning run and pause at human approval."""
    if not thread_id:
        thread_id = f"user_{uuid.uuid4().hex}"

    config = {"configurable": {"thread_id": thread_id}}

    result = travel_graph.invoke(
        {
            "messages": [HumanMessage(content=user_input)],
            "user_query": user_input,
            "guardrail_allowed": True,
            "guardrail_reason": "",
            "request_type": "full_itinerary",
            "selected_agents": [],
            "trip_constraints": _empty_constraints(),
            "supervisor_reasoning": "",
            "flight_results": "",
            "hotel_results": "",
            "weather_results": "",
            "budget_results": "",
            "itinerary": "",
            "approval_request": "",
            "approved": False,
            "human_feedback": "",
            "revision_count": 0,
            "final_response": "",
            "llm_calls": 0,
        },
        config=config,
    )

    return _serialize_result(result, thread_id)


def resume_travel_agent(
    thread_id: str,
    approved: bool,
    feedback: str = "",
):
    """Resume the paused LangGraph thread after human review."""
    if not thread_id:
        raise ValueError("thread_id is required to resume a travel plan.")

    config = {"configurable": {"thread_id": thread_id}}
    result = travel_graph.invoke(
        Command(
            resume={
                "approved": approved,
                "feedback": feedback.strip(),
            }
        ),
        config=config,
    )

    return _serialize_result(result, thread_id)