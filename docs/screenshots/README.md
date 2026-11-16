# Screenshots (real run, local model)

Captured on 17 Nov 2026 from a live session: `qwen2.5:7b-instruct` (32k context) on Ollama, one RTX 5080,
tools over MCP, no hosted API. Request: *"Hi, I am trying to gain weight and build muscle for my height, how
much should I weigh? How do I gain weight to reach ideal weight for my height and age? Build me a workout
and meal plan"* (profile: 26 y, male, 180 cm, 60 kg, India, omnivore, beginner, bodyweight only).

| File | What it shows |
|---|---|
| 01-answer-and-meal-plan.jpg | The answer (leads with the computed healthy weight 59.9-74.2 kg), the validated meal plan with macro rings |
| 02-workout-plan.jpg | The rule-checked workout: sessions, computed minutes, kcal and weekly sets per muscle |
| 03-calendar.jpg | Meal plan and workout scheduled on the calendar |
| 04-runs.jpg | Run history: agents, latency, tokens, actual cost ($0, local) and hosted-model estimate |
| 05-run-detail.jpg | One run: agent timeline and supervisor decisions with rule overrides highlighted |
| 06-agent-log.jpg | Agent table and the full event log of the run |
| 07-goals.jpg | Weight and workout goals (shared with the agents) |
| 08-saved-plans.jpg | Saved plans library |
| 09-profile.jpg | Profile: BMI, healthy range, energy target, protein range, usage totals, training settings |

The run took 143 s and 327k tokens: $0.00 on the local model, about $0.06 at gpt-4o-mini prices.
