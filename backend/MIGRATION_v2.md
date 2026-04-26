# SpecOps v2 schema migration

Source: 8 tables, mostly mutable.
Target: 5 tables, append-only, with read-time views.

## Mapping at a glance

| v1 table             | v2 destination        | Strategy                                                     |
|----------------------|-----------------------|--------------------------------------------------------------|
| `submissions`        | `operators`           | Each row → one INSERT. `rank='APPLICANT'` until promoted.    |
| `bot_roles`          | `operators`           | Joined into operators by email; sets `rank` field.           |
| `operations`         | `factories`           | The static "factory + shift" → `factories` row per factory. |
| `op_assignments`     | `operations`          | Each (op, email, role) → one `operations` row.               |
| `daily_reports`      | `events` (kind='DAILY_REPORT') | Each row → one event with payload of the numbers.   |
| `report_events`      | `events`              | Kind-tagged from existing `kind` column.                     |
| `attendance`         | `attendance`          | One-to-one. Add `factory_id`, `shift` from join on op_id.   |
| `report_reminders`   | `events` (kind='REMINDER_SENT') | Audit log preserved.                              |

## Identity reconciliation

### Operators
- v1 `submissions` rows have `pan_number` ciphertext. We need a `pan_hash`
  for v2 stable identity. Compute on migration: `pan_hash = sha256(decrypt(pan_number_enc))`.
- v1 `bot_roles` rows have `email` only. Match to a `submissions` row via
  email if one exists; otherwise create an operator row with
  `pan_hash = NULL` placeholder (we'll need to handle that). Better: any
  `bot_roles` entry without a submission becomes an `operators` row with
  `pan_hash = sha256("legacy:" + email)` so we have *some* stable key.
- Rank precedence: `bot_roles.role` wins over default `'APPLICANT'`.

### Factories vs operations
- Today's `operations` table conflates *factory identity* with *shift
  deployment*. v2 splits them:
  - The factory-level fields (`factory_name`, `location`, POCs, times)
    move to `factories`. One `factories` row per distinct `factory_name`.
  - The shift becomes a column on `operations` (the assignment table), not
    a column on `factories`. `factories.shift_count` defaults to 2 unless
    we have explicit info; we'll need to backfill manually for sites with
    A/B/C shifts.

### Operations (assignments)
- `op_assignments(op_id, email, role)` → `operations(operation_id,
  factory_id, shift, operator_pan_hash, role, state='ACTIVE', ...)`.
- `operator_pan_hash` resolved by joining email → operators.
- `operation_id = factory_id + shift_slug + pan_hash[:12]`.

### Attendance
- v1 `attendance` keyed on `(op_id, report_date, pan_hash)`. v2 keys on
  `(factory_id, shift, report_date, person_pan_hash)`. Migration parses
  v1 `op_id` (which is `factory_slug_shift`) to extract both.
- v1 `status` values normalize to UPPERCASE.

### Daily reports
- v1 `daily_reports` row → v2 `events(kind='DAILY_REPORT')`. Payload
  carries: chiefs, captains, operators, devices_*, sd_cards_*,
  actual_reporting_time. Submission metadata in top-level columns.

### Reminders
- v1 `report_reminders` (sent / skipped) → v2 `events(kind='REMINDER_SENT'
  or 'REMINDER_SKIPPED')`. Payload carries chief_email, channel, status,
  error.

## Migration steps (deliberate, gated)

1. **Build & ship `db_v2.init_db_v2()`**. New tables coexist with v1.
   Triggers / REVOKE not yet active. (This file: `backend/db_v2.py`.)
2. **Backfill script** (`scripts/migrate_v2.py`): reads v1, writes v2,
   one transaction per source table. Idempotent: skip rows whose stable
   key already exists in v2.
3. **Switch reads**: convert `routers/dashboard.py`, `routers/ops.py`,
   `routers/analytics.py` to query `v_*` views. v1 tables become unused
   for reads but still receive writes (parity period).
4. **Switch writes**: convert handlers to INSERT into v2 tables. v1
   tables stop receiving writes.
5. **Lock v2 to append-only**: enable triggers + REVOKE on v1 tables
   (so a stray query can't UPDATE / DELETE them).
6. **Burn-in window** (a few days): tail logs, look for divergence
   between sheets / dashboard before vs after.
7. **Drop v1 tables**: in a final migration, `DROP TABLE submissions,
   operations, op_assignments, bot_roles, daily_reports, report_events,
   report_reminders, attendance` (the v1 attendance — v2 has its own).

## Backwards compatibility

- During the parity period, the API serves the same shapes from the
  same paths. Frontend doesn't change.
- The `Operations` Google Sheet tab keeps populating throughout.
- If anything goes wrong in step 4 or 5, revert by switching reads back
  to v1 tables. v1 data is untouched until step 7.

## What this migration is NOT

- No frontend / URL changes (those land in a separate v2 nav redesign).
- No new features. Just the data layer.
- No multi-tenant schema. One spec-ops org per database, same as today.
