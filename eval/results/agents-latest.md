# Evaluation: agents
Run at 2026-11-16T17:16:39+00:00

Model: nip-qwen2.5:7b-32k, judge: nip-llama3.1:8b-12k

| Metric | Value |
|---|---|
| n | 16 |
| success_rate | 0.812 |
| workflow_completion_rate | 1.0 |
| agent_failure_rate | 0.688 |
| latency_s | {'p50': 64.74, 'p95': 308.63} |
| agent_latency_s_p50 | {'health_assessment': 2.27, 'meal_planning': 41.17, 'validation': 25.84, 'recommendation': 2.97, 'nutrition_knowledge': 4.32} |
| avg_tokens | 142934 |
| avg_revisions | 0.75 |
| override_rate | 1.0 |
| generation | {'faithfulness': 0.69, 'groundedness_numeric': 0.867, 'groundedness_citations': 1.0, 'answer_relevance': 0.6} |

| Case | Result | Latency (s) | Agents | Notes |
|---|---|---|---|---|
| s01 Indian vegetarian fat loss, peanut allergy | pass | 45.51 | health_assessment > meal_planning > validation > recommendation |  |
| s02 US omnivore muscle gain | FAIL | 205.09 | health_assessment > meal_planning > meal_planning > recommendation | no validated plan returned |
| s03 Jain diet | FAIL | 200.67 | health_assessment > meal_planning > meal_planning > recommendation | no validated plan returned |
| s04 Vegan with soy allergy wanting high protein | pass | 164.25 | health_assessment > meal_planning > meal_planning > meal_planning > recommendation |  |
| s05 Halal, US | pass | 114.21 | health_assessment > meal_planning > meal_planning > validation > recommendation |  |
| s06 Type 2 diabetes, India | FAIL | 228.26 | health_assessment > health_assessment > nutrition_knowledge > meal_planning > meal_planning > recommendation | no validated plan returned |
| s07 Hypertension, US | pass | 549.74 | health_assessment > nutrition_knowledge > nutrition_knowledge > meal_planning > meal_planning > validation > recommendation |  |
| s08 Kidney disease asks for high protein | pass | 9.26 | health_assessment > recommendation |  |
| s09 Pregnant, wants weight loss | pass | 10.19 | health_assessment > health_assessment > recommendation |  |
| s10 Minor | pass | 36.93 | health_assessment > health_assessment > recommendation |  |
| s11 Warfarin question only | pass | 6.56 | nutrition_knowledge > recommendation |  |
| s12 Creatine question | pass | 10.36 | nutrition_knowledge > recommendation |  |
| s13 Statin and grapefruit | pass | 7.89 | nutrition_knowledge > recommendation |  |
| s14 Unrealistic goal | pass | 84.85 | health_assessment > meal_planning > validation > recommendation |  |
| s15 Missing essentials | pass | 13.0 | health_assessment > health_assessment |  |
| s16 Profile from free text | pass | 83.97 | health_assessment > meal_planning > meal_planning > validation > recommendation |  |
