# Evaluation: retrieval
Run at 2026-11-16T05:27:26+00:00

50 labelled queries, embeddings: ollama

| Mode | Recall@5 | Precision@5 | MRR | nDCG@5 | Recall@10 | p95 ms |
|---|---|---|---|---|---|---|
| dense | 0.97 | 0.292 | 0.96 | 0.94 | 0.98 | 174.6 |
| sparse | 0.92 | 0.272 | 0.806 | 0.803 | 0.97 | 0.7 |
| hybrid | 0.96 | 0.288 | 0.917 | 0.903 | 0.97 | 33.9 |
