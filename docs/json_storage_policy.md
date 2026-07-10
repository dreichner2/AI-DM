# JSON Storage Policy

## Current Decision

AIDM stores structured payload columns as JSON-encoded `Text`. Production now
requires PostgreSQL through `postgresql+psycopg`, while SQLite remains a
supported local and test adapter. The text representation is retained so both
adapters share the same schema semantics and so session replay, export/import,
and legacy-data recovery continue to use the same application validation path.

Examples include player `stats`, `inventory`, and `character_sheet`; map
`map_data` and `metadata_json`; turn `rules_hint`; and session
`state_snapshot`. Structured writes are normalized by DTO or domain validators
and serialized with `safe_json_dumps`; reads use `safe_json_loads` or a
domain-specific equivalent.

This is a deliberate cross-adapter compatibility decision, not a claim that
database-native JSON has no value.

## Rules

- Validate the structured payload shape before persisting it.
- Tolerate malformed legacy JSON on reads and return an explicit safe default or
  validation error appropriate to the caller.
- Use existing DTO, schema, and normalization helpers rather than route-local
  `json.loads`/`json.dumps` calls.
- Preserve deterministic serialization where hashes, snapshots, replay,
  fixtures, or exported artifacts depend on stable output.
- Do not change an existing JSON-text column to native `db.JSON` in isolation.
  The migration must cover PostgreSQL production, SQLite local/test behavior,
  exports, imports, snapshots, and rollback.

## Native JSON Migration Trigger

Reconsider native JSON only when a concrete feature needs database-side JSON
queries, indexes, or constraints that cannot be provided safely through the
current model. PostgreSQL adoption by itself is no longer the trigger.

Any proposal must include:

- the query, index, or integrity requirement that justifies the change;
- an explicit SQLite strategy for local and test runs;
- side-by-side columns or another reversible backfill path that parses existing
  values through current safe JSON helpers;
- DTO and write-path changes before old columns are removed; and
- replay, export/import, snapshot, migration, and frontend contract tests across
  the supported adapters.
