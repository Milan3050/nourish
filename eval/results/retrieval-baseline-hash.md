# Evaluation: retrieval (hash embeddings, lexical baseline)
Run at 2026-11-15T18:24:07+00:00

50 labelled queries, embeddings: hash

| Mode | Recall@5 | Precision@5 | MRR | nDCG@5 | Recall@10 | p95 ms |
|---|---|---|---|---|---|---|
| dense | 0.72 | 0.212 | 0.603 | 0.593 | 0.773 | 3.3 |
| sparse | 0.92 | 0.272 | 0.806 | 0.803 | 0.97 | 1.2 |
| hybrid | 0.87 | 0.26 | 0.724 | 0.726 | 0.95 | 5.3 |
