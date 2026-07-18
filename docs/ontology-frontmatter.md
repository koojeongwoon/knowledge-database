# Explicit ontology frontmatter

Phase 1 reads only human-authored `ontology` frontmatter. It does not infer
relations from Markdown prose and it does not affect direct retrieval.

```yaml
---
ontology:
  concepts:
    - id: knowledge-service
      name: Knowledge Service
      kind: service
      aliases: [KB service]
    - id: postgresql
      name: PostgreSQL
      kind: technology
  relations:
    - subject: knowledge-service
      predicate: uses
      object: postgresql
      status: asserted
      confidence: 1.0
      scope:
        environment: production
      valid_from: 2026-07-01T00:00:00Z
      evidence: Knowledge Service uses PostgreSQL in production.
      evidence_location:
        heading: Architecture
  document_concepts:
    - concept: knowledge-service
    - concept: postgresql
---
```

Allowed predicates are `uses`, `depends_on`, `is_a`, `part_of`, `supersedes`,
`contradicts`, `prohibits`, `requires`, and `related_to`.

Relation statuses are `inferred`, `pending`, `asserted`, `rejected`, `stale`,
and `revoked`. Legacy explicit values `approved`, `draft`, and `deprecated`
remain accepted and normalize to `asserted`, `pending`, and `revoked`.

Relations and their evidence are stored separately. A relation owns lifecycle,
scope, validity, and review state; each evidence row owns source revision,
source location, confidence, extractor/model/prompt versions, and extraction
time. Explicit frontmatter is recorded as `extractor_type=human`. Automatic LLM
extraction remains disabled in this phase.

Review identity and timestamps are not accepted from document frontmatter.
They must be written later by an authenticated review action so a document
author cannot forge approval metadata. In particular, asserted frontmatter by
itself is not sufficient authorization for a hard rule.

All five rollout flags default to false. Phase 1 does not call the extractor
from the indexing service and does not call ontology storage or retrieval from
the direct search service.

`OntologyShadowService` has three safe states: disabled is a strict no-op,
shadow-only parses and reports counts without writes, and persistence happens
only when both shadow and indexing flags are enabled.

Phase 2 calls the shadow service only after the direct document transaction
succeeds. Shadow validation, persistence, and telemetry failures are contained;
they cannot fail direct indexing. Enabled runs append an observation to
`knowledge_ontology_shadow_events`. With indexing disabled, no concept or
relation rows are changed.

Concept provenance is many-to-many through
`knowledge_ontology_concept_sources`. Re-indexing or deleting one file removes
only that file's aliases, relations, document links, and concept-source rows;
concepts still declared by other files are retained.
