# PostgreSQL 18 Upgrade Runbook

This runbook upgrades AIDM from PostgreSQL 17 to PostgreSQL 18 without treating
an in-place major upgrade as a reversible operation. Keep writes paused until
every post-upgrade check passes. PostgreSQL major-version downgrades are not
supported; rollback means reconnecting AIDM to an untouched PostgreSQL 17
database or restoring a PostgreSQL 17-native backup into a new PostgreSQL 17
database.

## Current baseline

- Hosted staging database: PostgreSQL 17.10.
- Target: PostgreSQL 18.4.
- Alembic revision: `0029_players_account_fk`.
- Baseline schema: 37 public tables, standard `plpgsql` extension only.
- The application uses psycopg 3.3.4 and SQLAlchemy 2.0.51 and enables
  `pool_pre_ping` for PostgreSQL connections.

Record a new baseline immediately before the maintenance window. Do not rely on
the counts above if the database has received additional writes.

## Preconditions

1. The exact application commit intended for release has passed backend,
   frontend, migration, security, browser, and production-startup checks against
   PostgreSQL 18.4.
2. The managed provider reports the PostgreSQL 17 database healthy and confirms
   point-in-time recovery or a restorable snapshot is available.
3. A PostgreSQL 18 clone rehearsal has passed. Creating a paid managed clone
   requires explicit cost approval; local restore evidence does not replace this
   final provider-level rehearsal.
4. No Alembic migration is pending and `flask db check` reports no metadata
   drift.
5. The operator has a tested way to pause all application writes and a tested
   PostgreSQL 17 rollback database or snapshot.

## Two independent backup artifacts

Create both artifacts. Store them encrypted outside the service filesystem,
restrict them to mode `0600`, record SHA-256 checksums, and verify each with
`pg_restore --list`.

### PostgreSQL 17 rollback backup

Use a PostgreSQL 17 `pg_dump` client against the PostgreSQL 17 source. Restore
this archive into a separate PostgreSQL 17 target and run the same schema,
row-count, sequence, index, constraint, migration, and startup checks used for
the forward rehearsal. This is the portable rollback artifact.

```bash
export PGHOST=db.example.internal PGPORT=5432 PGDATABASE=aidm PGUSER=aidm_backup
export PGPASSFILE=/secure/pgpass
/path/to/postgresql-17/bin/pg_dump \
  --format=custom --no-owner --no-privileges \
  --file /secure/aidm-pg17-rollback.dump \
  --dbname "$PGDATABASE"
chmod 600 /secure/aidm-pg17-rollback.dump
shasum -a 256 /secure/aidm-pg17-rollback.dump
/path/to/postgresql-17/bin/pg_restore --list /secure/aidm-pg17-rollback.dump >/dev/null
```

### PostgreSQL 18 forward-restore backup

Use the repository drill with PostgreSQL 18 client tools, a read-only source,
and a separately created empty PostgreSQL 18 target. Prefer mode-`0600` URI files
so credentials never appear in shell history.

```bash
make postgres-backup-restore-drill \
  POSTGRES_BACKUP_RESTORE_DRILL_ARGS="\
    --source-uri-file /secure/source-pg17.uri \
    --empty-target-uri-file /secure/empty-target-pg18.uri \
    --pg-dump /path/to/postgresql-18/bin/pg_dump \
    --pg-restore /path/to/postgresql-18/bin/pg_restore \
    --expected-source-major 17 \
    --expected-target-major 18 \
    --expected-pg-dump-major 18 \
    --expected-pg-restore-major 18"
```

The drill refuses identical endpoints, requires an Alembic-managed nonempty
source, verifies the target is empty twice, shares one exported read-only
snapshot between inspection and `pg_dump`, restores in one transaction, and
compares tables, row counts, sequence state, migrations, invalid indexes, and
unvalidated constraints. Preserve its mode-`0600` Markdown and JSON evidence.

## Managed clone rehearsal

1. Clone the PostgreSQL 17 database using the provider's supported snapshot or
   recovery workflow.
2. Upgrade only the clone to PostgreSQL 18.
3. Confirm the server reports the expected 18.x version.
4. Point a non-public AIDM staging instance at the clone.
5. Run:

   ```bash
   python -m flask --app aidm_server.main:create_app db upgrade
   python -m flask --app aidm_server.main:create_app db check
   python scripts/deployment_readiness_check.py --allow-fallback-provider
   make hosted-cookie-auth-smoke HOSTED_COOKIE_AUTH_SMOKE_ARGS="..."
   make security-forbidden-smoke SECURITY_FORBIDDEN_SMOKE_ARGS="..."
   make session-export-import-smoke SESSION_EXPORT_IMPORT_SMOKE_ARGS="..."
   ```

6. Verify health, metrics, security headers, authentication, WebSocket delivery,
   concurrency fencing, export/import, and a real threaded Gunicorn startup.
7. Compare the clone with the recorded source baseline. Any unexplained count,
   sequence, schema, index, constraint, or Alembic difference is a stop condition.

## Production cutover

1. Announce the maintenance window and stop or maintenance-gate every writer.
2. Confirm active writes have drained.
3. Record the final PostgreSQL 17 server version, Alembic revision, table counts,
   sequence state, invalid-index count, and unvalidated-constraint count.
4. Create fresh PostgreSQL 17-native rollback and PostgreSQL 18-client forward
   archives, then verify their modes, checksums, and restore lists.
5. Confirm the managed snapshot/PITR restore point and the untouched PostgreSQL
   17 rollback target.
6. Run the provider's supported PostgreSQL 17-to-18 upgrade. Do not change the
   application release at the same time unless the provider requires it.
7. Restart AIDM so all pooled connections are new, even though `pool_pre_ping`
   is enabled.
8. Apply Alembic migrations and perform every validation below while writes
   remain paused.
9. Reopen writes only after the operator signs off the complete evidence set.

## Post-upgrade validation

- Server version is PostgreSQL 18 and matches the approved target release.
- Alembic is at the expected head and `flask db check` is clean.
- Public table set and row counts match the final PostgreSQL 17 baseline.
- Sequence configuration and last-value/called state match.
- No unexpected invalid indexes or unvalidated constraints exist.
- AIDM production bootstrap and threaded Gunicorn startup pass.
- `/api/health`, authenticated metrics, security headers, account/cookie auth,
  forbidden-response behavior, WebSockets, concurrency fencing, and
  export/import pass.
- Provider dashboards show no connection, storage, replication, or error-rate
  regression.

## Rollback

If any validation fails, keep writes paused. Do not try to downgrade the
PostgreSQL 18 data directory.

1. Stop AIDM.
2. Select the untouched PostgreSQL 17 clone/snapshot, or create a new PostgreSQL
   17 database and restore the verified PostgreSQL 17-native custom archive with
   `--exit-on-error --single-transaction --no-owner --no-privileges`.
3. Repoint `AIDM_DATABASE_URI` to the PostgreSQL 17 rollback database.
4. Restart the exact pre-upgrade or compatibility-tested AIDM release.
5. Re-run migration-head, metadata, count, sequence, index, constraint,
   production-startup, auth, WebSocket, and export/import checks.
6. Reopen writes only after the PostgreSQL 17 service is confirmed healthy.

Writes accepted by PostgreSQL 18 after cutover are not automatically
backward-portable. This is why writes remain paused until validation completes.
If writes were accidentally reopened, preserve the PostgreSQL 18 database and
reconcile those records with an application-level export or a forward fix.

## Cleanup

Keep the PostgreSQL 17 rollback database, both verified archives, their
checksums, and provider recovery point until the agreed observation window has
passed. Then delete credentials and temporary databases securely while retaining
non-secret evidence and operator signoff.
