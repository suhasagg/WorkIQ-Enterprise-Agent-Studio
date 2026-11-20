# Evaluation report

Evaluations are part of the product (`/evals`, `app/evals/`). They run against the live retrieval pipeline, the
compiled graph and the policy layer, store per-case results, apply thresholds and compare each run with a baseline.

**Setup for the numbers below:** freshly seeded `meridian` workspace (13 synthetic documents, 58 chunks),
deterministic local model, local hash embeddings (384 dimensions), hybrid retrieval with reranking.

## Results

| Dataset | Cases | Metric | Result | Threshold | Gate |
|---|---|---|---|---|---|
| Retrieval quality | 12 | Recall@5 | 1.000 | ≥ 0.85 | pass |
| | | MRR | 0.958 | ≥ 0.70 | pass |
| | | NDCG@5 | 0.975 | ≥ 0.70 | pass |
| Agent workflows | 8 | Task success | 1.000 | ≥ 0.80 | pass |
| | | Groundedness | 1.000 | ≥ 0.90 | pass |
| | | Citation correctness | 1.000 | ≥ 0.90 | pass |
| | | Completeness | 1.000 | ≥ 0.90 | pass |
| Safety and guardrails | 8 | Pass rate | 8 / 8 | 100% | pass |

### Experiment: vector-only retrieval without reranking

Run from Evaluation Studio with *Vector only* and reranking off, compared with the hybrid baseline:

| Metric | Baseline (hybrid + rerank) | Vector only | Change |
|---|---|---|---|
| Recall@5 | 1.000 | 0.917 | −8.3 pt |
| MRR | 0.958 | 0.875 | −8.3 pt |
| NDCG@5 | 0.975 | 0.875 | −10.0 pt |

11 of 12 cases passed and the run was flagged as a regression (screenshot 28). The thresholds were still met, which
is why the gate itself passed.

## What each dataset checks

**Retrieval quality** (`retrieval_quality.json`): hand-labelled queries with the documents and sections that should
be returned. Scored with Recall@K, MRR and NDCG@K.

**Agent workflows** (`agent_workflows.json`): the four business workflows plus free-form questions, including one
that the documents cannot answer. Each case runs the real graph in propose-only mode (no approvals, no writes) and
checks the classified intent, required report sections, required terms, minimum citations and actions, that no write
was executed, and that the agent abstains when it should. Groundedness and citation correctness compare each cited
claim with the passage it cites.

**Safety and guardrails** (`safety_guardrails.json`): prompt injection in a document, restricted-document visibility
for operator and admin, workspace isolation, write-approval policy, viewer write denial, blocked Work IQ paths and
secret redaction.

## How to read these numbers

- The corpus is small and synthetic and the questions were written alongside it. Perfect or near-perfect scores are
  expected and show that the pipeline, the checks and the gates work. They are **not** a benchmark.
- The model is deterministic. Groundedness is high partly because the local model composes answers from extracted
  sentences; an LLM would paraphrase and needs the same check more.
- Hash embeddings capture word overlap, not meaning. The vector-only experiment shows this: the keyword channel and
  the reranker are doing real work.
- Groundedness is a lexical support score, not an LLM judge.

## Reproduce

```bash
make reset && make api        # fresh demo; baseline runs are created during seeding
# then in the UI: Evaluations → Run evaluation, or
curl -X POST localhost:8140/api/evals/runs -H "authorization: Bearer $TOKEN" \
     -H 'content-type: application/json' -d '{"dataset":"retrieval_quality"}'
```

The same three datasets are executed and asserted in `tests/test_integration.py::test_evaluation_datasets_pass_their_gates`.
