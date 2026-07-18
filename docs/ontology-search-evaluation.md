# Ontology search evaluation

Phase 0 freezes the existing direct-search baseline before ontology retrieval can affect users.

## Separation rule

- `direct_paths` are scored independently with the existing Top-1, Recall@5, MRR, and no-answer metrics.
- `context_paths` never occupy direct ranking slots during shadow or context-only rollout.
- `forbidden_paths` measure unsafe or lifecycle-invalid exposure.
- expected relations and rules are scored independently from document relevance.
- `evidence_paths`, `rationale`, and `review_status` preserve the human review trail;
  only `verified` cases belong in a promotion gate.

## Case contract

Use `tests/ontology_quality_cases.example.json` as the shape only. Replace example paths with independently reviewed real knowledge paths in a private development or blind set.

Required case categories are `relation`, `lifecycle`, `contradiction`, `prohibition`, `concept-expansion`, and `no-answer`. Do not tune and report final quality on the same cases.

`forbidden_paths` means a document must not be exposed for that query. A prohibited
decision such as "engine failure must never become Clean" belongs in
`expected_rules`, not in `forbidden_paths`.

## Promotion rule

1. Freeze the current direct report and configuration.
2. Run ontology in shadow mode and produce direct and ontology outputs separately.
3. Reject promotion when any direct metric exceeds the allowed regression in `tests/ontology_quality_gates.json`.
4. Reject promotion when forbidden exposure is non-zero.
5. Promote shadow to context-only before considering ranking or hard-rule effects.

Check a future candidate report against the frozen direct baseline:

```bash
python main.py check-direct-regression \
  --baseline tests/direct_search_baseline.json \
  --candidate /path/to/candidate-direct-report.json \
  --gates tests/ontology_quality_gates.json
```

The command exits non-zero when any protected direct metric exceeds its allowed drop.
