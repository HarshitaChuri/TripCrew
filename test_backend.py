from backend import run_travel_agent

print("Running travel agent... (this can take 15-30 seconds)")
print("-" * 60)

result = run_travel_agent("Plan a 4 day trip to Dubai from Mumbai")

print("requires_approval:", result["requires_approval"])
print("selected_agents:", result["selected_agents"])
print("guardrail_allowed:", result["guardrail_allowed"])
print("llm_calls:", result["llm_calls"])
print("-" * 60)
print("DRAFT ANSWER (first 500 chars):")
print(result["answer"][:500])