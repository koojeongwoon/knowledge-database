# Blind search evaluation

The committed development set is only for implementation and regression checks. It must not be reported as evidence of general search quality.

## Roles

1. An independent evaluator samples documents and writes at least 50 blind queries.
2. The evaluator stores `tests/search_quality_blind_queries.json` and `tests/search_quality_blind_answers.json` outside Git.
3. The retrieval implementer freezes code, configuration, image tag, and `tests/search_quality_gates.json` before receiving the answer key.
4. Search predictions are generated once. Only then is the answer key supplied for scoring.

The evaluator should stratify cases by `qa/topics`, visibility, document date, exact/semantic/cross-language/acronym/mixed-language query type, and no-answer cases. Documents used as answers in the development set are forbidden in the blind set.

## Commands

Generate the SHA-256 fingerprint that belongs in the private answer file:

```bash
python -c 'from src.retrieval.evaluation import load_blind_queries,blind_query_fingerprint; print(blind_query_fingerprint(load_blind_queries("tests/search_quality_blind_queries.json")))'
```

Freeze predictions without loading the answer key:

```bash
python main.py run-blind-search \
  --owner-id OWNER_ID \
  --queries tests/search_quality_blind_queries.json \
  --output tests/search_quality_blind_predictions.json
```

After the answer key is released, score the frozen predictions:

```bash
python main.py score-blind-search \
  --queries tests/search_quality_blind_queries.json \
  --predictions tests/search_quality_blind_predictions.json \
  --answers tests/search_quality_blind_answers.json \
  --output tests/search_quality_blind_report.json
```

Scoring rejects mismatched fingerprints, missing or extra case IDs, answer fields embedded in the query file, and document overlap with the development set.
