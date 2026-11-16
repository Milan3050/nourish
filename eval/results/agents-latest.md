# Evaluation: agents
Run at 2026-11-16T07:00:14+00:00

Model: nip-qwen2.5:7b-32k, judge: nip-llama3.1:8b-12k

| Metric | Value |
|---|---|
| n | 16 |
| success_rate | 0.625 |
| workflow_completion_rate | 0.875 |
| agent_failure_rate | 0.5 |
| latency_s | {'p50': 28.97, 'p95': 268.83} |
| agent_latency_s_p50 | {'health_assessment': 2.12, 'meal_planning': 30.35, 'validation': 9.73, 'recommendation': 4.77, 'nutrition_knowledge': 4.72} |
| avg_tokens | 160176 |
| avg_revisions | 0.81 |
| override_rate | 1.0 |
| generation | {'faithfulness': 0.722, 'groundedness_numeric': 0.949, 'groundedness_citations': 0.792, 'answer_relevance': 0.615} |

| Case | Result | Latency (s) | Agents | Notes |
|---|---|---|---|---|
| s01 Indian vegetarian fat loss, peanut allergy | pass | 43.63 | health_assessment > meal_planning > validation > recommendation |  |
| s02 US omnivore muscle gain | FAIL | 246.17 | health_assessment > meal_planning > meal_planning > meal_planning > recommendation | no validated plan returned |
| s03 Jain diet | pass | 25.16 | health_assessment > meal_planning > validation > recommendation |  |
| s04 Vegan with soy allergy wanting high protein | pass | 191.7 | health_assessment > meal_planning > meal_planning > meal_planning > recommendation |  |
| s05 Halal, US | FAIL | 280.81 | health_assessment > meal_planning > meal_planning > recommendation | no validated plan returned |
| s06 Type 2 diabetes, India | FAIL | 178.92 | health_assessment > health_assessment > nutrition_knowledge > meal_planning > meal_planning > recommendation | status failed not in ['answered']; no validated plan returned |
| s07 Hypertension, US | FAIL | 166.72 | health_assessment > meal_planning > meal_planning > meal_planning > recommendation | no validated plan returned |
| s08 Kidney disease asks for high protein | FAIL | 27.03 | health_assessment > recommendation | status failed not in ['answered'] |
| s09 Pregnant, wants weight loss | pass | 10.96 | health_assessment > health_assessment > recommendation |  |
| s10 Minor | pass | 9.59 | health_assessment > health_assessment > recommendation |  |
| s11 Warfarin question only | pass | 13.19 | nutrition_knowledge > recommendation |  |
| s12 Creatine question | pass | 10.73 | nutrition_knowledge > recommendation |  |
| s13 Statin and grapefruit | pass | 6.81 | nutrition_knowledge > recommendation |  |
| s14 Unrealistic goal | pass | 32.78 | health_assessment > meal_planning > validation > recommendation |  |
| s15 Missing essentials | pass | 18.81 | health_assessment > health_assessment |  |
| s16 Profile from free text | FAIL | 262.38 | health_assessment > meal_planning > meal_planning > recommendation | no validated plan returned |
