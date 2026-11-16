# Evaluation: agents
Run at 2026-11-16T06:02:48+00:00

Model: nip-qwen2.5:7b-32k, judge: nip-llama3.1:8b-12k

| Metric | Value |
|---|---|
| n | 16 |
| success_rate | 0.688 |
| workflow_completion_rate | 0.938 |
| agent_failure_rate | 0.438 |
| latency_s | {'p50': 22.75, 'p95': 219.56} |
| agent_latency_s_p50 | {'health_assessment': 1.93, 'meal_planning': 20.07, 'recommendation': 2.16, 'validation': 20.59, 'nutrition_knowledge': 2.09} |
| avg_tokens | 90251 |
| avg_revisions | 0.44 |
| override_rate | 1.0 |
| generation | {'faithfulness': 0.699, 'groundedness_numeric': 1.0, 'groundedness_citations': 0.762, 'answer_relevance': 0.554} |

| Case | Result | Latency (s) | Agents | Notes |
|---|---|---|---|---|
| s01 Indian vegetarian fat loss, peanut allergy | FAIL | 159.77 | health_assessment > meal_planning > meal_planning > meal_planning > recommendation | validation did not run; no validated plan returned |
| s02 US omnivore muscle gain | FAIL | 265.61 | health_assessment > meal_planning > meal_planning > recommendation | no validated plan returned |
| s03 Jain diet | pass | 37.35 | health_assessment > meal_planning > validation > recommendation |  |
| s04 Vegan with soy allergy wanting high protein | pass | 91.17 | health_assessment > meal_planning > meal_planning > validation > recommendation |  |
| s05 Halal, US | FAIL | 199.83 | health_assessment > meal_planning > validation > meal_planning > meal_planning > recommendation | no validated plan returned |
| s06 Type 2 diabetes, India | FAIL | 26.46 | health_assessment > health_assessment > recommendation | status failed not in ['answered']; no validated plan returned; not enough citations |
| s07 Hypertension, US | FAIL | 14.34 | health_assessment > nutrition_knowledge > recommendation | no validated plan returned |
| s08 Kidney disease asks for high protein | pass | 12.17 | health_assessment > recommendation |  |
| s09 Pregnant, wants weight loss | pass | 6.47 | health_assessment > recommendation |  |
| s10 Minor | pass | 11.51 | health_assessment > health_assessment > recommendation |  |
| s11 Warfarin question only | pass | 6.03 | nutrition_knowledge > recommendation |  |
| s12 Creatine question | pass | 4.61 | nutrition_knowledge > recommendation |  |
| s13 Statin and grapefruit | pass | 5.1 | nutrition_knowledge > recommendation |  |
| s14 Unrealistic goal | pass | 41.35 | health_assessment > meal_planning > validation > recommendation |  |
| s15 Missing essentials | pass | 22.75 | health_assessment > health_assessment |  |
| s16 Profile from free text | pass | 73.15 | health_assessment > meal_planning > validation > recommendation |  |
