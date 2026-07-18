# Ontology relation lifecycle and roadmap boundary

## Storage contract

Canonical concepts are resolved before a relation is created. The relation
stores `subject_concept_id`, `predicate`, `object_concept_id`, owner, lifecycle
status, scope, temporal validity, and review history. Evidence is a separate
many-to-one record so evidence spans and extractor runs are not packed into the
relation row or mistaken for asserted truth. Explicit snapshots remain
source-scoped; cross-document logical relation aggregation belongs to the later
entity-resolution and candidate-review phase.

Statuses are:

- `inferred`: an extractor proposed the relation.
- `pending`: the relation is waiting for review.
- `asserted`: a human-authored or reviewed relation may affect approved paths.
- `rejected`: the proposed relation was judged incorrect.
- `stale`: its source revision or extraction contract changed.
- `revoked`: a previously asserted relation was withdrawn.

`prohibits`, `supersedes`, security filters, and hard deny decisions require an
asserted human-reviewed relation. Repeated LLM evidence can raise review
priority but cannot independently authorize a hard rule.

## Evidence and reproducibility

Each evidence record includes source path and revision, text and location,
evidence hash, confidence, extractor type, model and prompt versions, ontology
schema version, and timestamps. Source changes replace only that source's
explicit snapshot. Future automatic extraction must mark affected asserted
relations stale for review rather than silently deleting or overwriting them.

## Scope and visibility

Scope may identify project, tenant, environment, or software version. Temporal
validity uses `valid_from` and `valid_to`. Relation and evidence visibility must
never exceed the intersection of the subject, object, and source permissions;
owner filtering remains mandatory before traversal.

## Roadmap boundary

Implemented and mergeable before data collection:

1. Frozen direct-search evaluation and ontology-specific quality contracts.
2. Owner-scoped concepts, aliases, explicit relations, and document links.
3. Changed-file shadow side path, deletion cleanup, and best-effort telemetry.
4. Relation lifecycle, scope, validity, review fields, source revision, and
   normalized evidence storage.
5. All ontology influence flags remain off by default; direct retrieval is
   unchanged.

Not enabled until the data-collection phase is explicitly started:

1. LLM concept/relation extraction from prose.
2. Candidate review queues and approval/rejection UI.
3. Automatic entity resolution and alias merge suggestions.
4. Ontology context, ranking, or hard-rule effects on search results.
5. Promotion decisions based on measured, human-labeled precision and recall.
