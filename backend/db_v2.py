"""SpecOps v2 schema: 5 append-only tables + read-time views.

Architecture
------------
- Forms write to source tables only (INSERT). No UPDATE or DELETE, ever.
- Corrections = new INSERT with the same stable key (pan_hash, factory_id, ...).
- Views compute "current state" with DISTINCT ON (key) ORDER BY key, id DESC.
- Append-only is enforced two ways:
    1. REVOKE UPDATE, DELETE on the application role.
    2. BEFORE UPDATE OR DELETE triggers on every table (defence in depth).

Lives in its own Postgres schema (`v2`) so it can coexist with the
legacy v1 tables in `public` during the migration window. After the
parity period, we either:
  - keep v2 as a schema (qualify references as `v2.operators`, etc.); or
  - move tables back to `public` with ALTER TABLE … SET SCHEMA public,
    after the v1 originals are dropped.

Tables
------
  1. operators   — every human (chiefs, captains, operators, applicants, viewers)
  2. factories   — physical factories with visit-report data
  3. operations  — assignment of an operator to a factory + shift
  4. attendance  — per-person attendance per shift per date
  5. events      — daily report numbers + ad-hoc ops events

Identity strategy
-----------------
- Operators identified by `pan_hash` (PAN or Aadhaar, hashed). Email is
  optional (operators on the floor often don't have one).
- Factories by `factory_id` (slug of factory_name).
- Operations by `operation_id` (slug = factory_id + shift + pan_hash).
- Each row carries `id BIGSERIAL` so "latest by stable key" is
  unambiguous: ORDER BY <stable_key>, id DESC.

Read shape
----------
Code never selects directly from the source tables (except for audit /
debug). API handlers query the views, which already collapse history to
"current". That keeps callers out of the append-only mental model.
"""

# ---------------------------------------------------------------------------
# Schema
# ---------------------------------------------------------------------------

CREATE_SCHEMA = "CREATE SCHEMA IF NOT EXISTS v2;"


# ---------------------------------------------------------------------------
# Tables (all in the `v2` schema)
# ---------------------------------------------------------------------------

CREATE_OPERATORS = """
CREATE TABLE IF NOT EXISTS v2.operators (
    id                  BIGSERIAL PRIMARY KEY,

    -- Stable identity. PAN/Aadhaar hashed (never stored raw + non-encrypted).
    pan_hash            TEXT NOT NULL,

    -- Optional auth identity. NULL for operators on the floor.
    email               TEXT,

    -- Identity (always UPPERCASE in storage).
    full_name           TEXT NOT NULL,
    whatsapp            TEXT,
    google_id           TEXT,

    -- Social handles.
    telegram_id         TEXT,
    discord_id          TEXT,
    twitter_id          TEXT,
    referred_by         TEXT,

    -- Capability + intent.
    languages           TEXT,                -- comma-joined; small list, no need for array
    hardest_problem     TEXT,
    health_notes        TEXT,

    -- Address.
    address_line1       TEXT,
    address_line2       TEXT,
    pincode             TEXT,
    city                TEXT,
    state_name          TEXT,                -- "state" is reserved-ish; use state_name

    -- Banking. Account + PAN ciphertext, IFSC plaintext.
    upi_id              TEXT,
    beneficiary_name    TEXT,
    account_number_enc  BYTEA,
    ifsc_code           TEXT,
    bank_name           TEXT,
    branch_name         TEXT,
    pan_number_enc      BYTEA,

    -- Storage URLs.
    pan_card_url        TEXT,
    profile_picture_url TEXT,
    intro_video_url     TEXT,

    -- Consent.
    consented           BOOLEAN NOT NULL DEFAULT FALSE,
    consented_terms     BOOLEAN NOT NULL DEFAULT FALSE,

    -- Operational state.
    rank                TEXT NOT NULL CHECK (rank IN
                          ('FREDDY','GENERAL','CHIEF','CAPTAIN','OPERATOR','VIEWER','APPLICANT')),
    op_state            TEXT NOT NULL DEFAULT 'ACTIVE'
                          CHECK (op_state IN ('ACTIVE','INACTIVE')),

    -- Append metadata.
    ts                  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    submitted_by        TEXT NOT NULL          -- 'self' for self-onboarding, else email
);

CREATE INDEX IF NOT EXISTS operators_pan_id     ON v2.operators (pan_hash, id DESC);
CREATE INDEX IF NOT EXISTS operators_email      ON v2.operators (email)      WHERE email IS NOT NULL;
CREATE INDEX IF NOT EXISTS operators_rank_state ON v2.operators (rank, op_state);
"""

CREATE_FACTORIES = """
CREATE TABLE IF NOT EXISTS v2.factories (
    id                    BIGSERIAL PRIMARY KEY,

    -- Stable identity (slug of factory_name).
    factory_id            TEXT NOT NULL,
    factory_name          TEXT NOT NULL,            -- always UPPERCASE

    -- Shift configuration. 2 -> {AM, PM}, 3 -> {SHIFT A, SHIFT B, SHIFT C}.
    shift_count           SMALLINT NOT NULL CHECK (shift_count IN (2, 3)),

    -- Building details: {"buildings": N, "floors": N, "sections": N}.
    units                 JSONB NOT NULL DEFAULT '{}'::jsonb,

    location_link         TEXT,

    -- Points of contact: {"name": "...", "phone": "...", "role": "..."}.
    poc1                  JSONB NOT NULL DEFAULT '{}'::jsonb,
    poc2                  JSONB NOT NULL DEFAULT '{}'::jsonb,

    -- Site inspection.
    inspection_chief_email TEXT,                    -- references operators.email
    inspection_date        DATE,
    inspection_report_url  TEXT,                    -- file in storage

    -- Operational rules.
    protocols              TEXT,                    -- free-form factory protocols
    charging_setup         JSONB NOT NULL DEFAULT '{}'::jsonb,
                                                    -- {"load_balancing": "...", "ports": N, "extensions": "..."}
    worker_count           INT,
    floor_escalations      JSONB NOT NULL DEFAULT '[]'::jsonb,
                                                    -- [{"floor": N, "name": "...", "phone": "..."}, ...]
    compliance_escalation  JSONB NOT NULL DEFAULT '{}'::jsonb,
                                                    -- {"name": "...", "phone": "...", "scope": "..."}

    -- Visual layout.
    photos                 TEXT[] NOT NULL DEFAULT '{}'::text[],
    videos                 TEXT[] NOT NULL DEFAULT '{}'::text[],
    layout_asset_url       TEXT,                    -- SVG or image

    -- Operational state.
    state                  TEXT NOT NULL DEFAULT 'ACTIVE'
                              CHECK (state IN ('ACTIVE','INACTIVE')),

    -- Append metadata.
    ts                     TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    submitted_by           TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS factories_id_idx    ON v2.factories (factory_id, id DESC);
CREATE INDEX IF NOT EXISTS factories_state_idx ON v2.factories (state);
"""

CREATE_OPERATIONS = """
-- An "operation" row is one operator assigned to one (factory, shift).
-- Multiple rows for the same factory_id+shift = the deployment roster.
CREATE TABLE IF NOT EXISTS v2.operations (
    id                  BIGSERIAL PRIMARY KEY,

    -- Stable identity = factory_id + shift_slug + pan_hash.
    operation_id        TEXT NOT NULL,

    factory_id          TEXT NOT NULL,
    shift               TEXT NOT NULL,            -- 'AM','PM','SHIFT A','SHIFT B','SHIFT C'
    operator_pan_hash   TEXT NOT NULL,

    -- The role this operator plays on THIS operation. Distinct from
    -- operators.rank — a captain on his factory might be a chief on a
    -- different deployment.
    role                TEXT NOT NULL CHECK (role IN ('CHIEF','CAPTAIN','OPERATOR')),

    -- Operational state. Removing an assignment = INSERT with state='INACTIVE'.
    state               TEXT NOT NULL DEFAULT 'ACTIVE'
                          CHECK (state IN ('ACTIVE','INACTIVE')),

    -- Append metadata.
    ts                  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    submitted_by        TEXT NOT NULL              -- email of the chief/general doing the assigning
);

CREATE INDEX IF NOT EXISTS operations_id_idx       ON v2.operations (operation_id, id DESC);
CREATE INDEX IF NOT EXISTS operations_factory_idx  ON v2.operations (factory_id, shift, id DESC);
CREATE INDEX IF NOT EXISTS operations_operator_idx ON v2.operations (operator_pan_hash, id DESC);
"""

CREATE_ATTENDANCE = """
CREATE TABLE IF NOT EXISTS v2.attendance (
    id                  BIGSERIAL PRIMARY KEY,

    -- Composite stable key for "this person on this shift on this day".
    factory_id          TEXT NOT NULL,
    shift               TEXT NOT NULL,
    report_date         DATE NOT NULL,
    person_pan_hash     TEXT NOT NULL,

    -- Snapshot of person info at submission time (so attendance reads don't
    -- depend on operators having been onboarded yet).
    full_name           TEXT NOT NULL,
    phone               TEXT,
    person_role         TEXT NOT NULL CHECK (person_role IN ('CHIEF','CAPTAIN','OPERATOR')),

    -- Photo proof (no geo verification — review chain handles trust).
    photo_url           TEXT,

    -- Validation lifecycle. Status changes = new rows with new ts.
    -- Approval chain follows the rank ladder one step up:
    --   OPERATOR  → reviewed by CAPTAIN
    --   CAPTAIN   → reviewed by CHIEF
    --   CHIEF     → reviewed by GENERAL
    -- The validator_role on the row is who is *expected* to act on it next.
    status              TEXT NOT NULL DEFAULT 'PENDING'
                          CHECK (status IN ('PENDING','CONFIRMED','REJECTED')),
    validator_role      TEXT CHECK (validator_role IN ('CAPTAIN','CHIEF','GENERAL')),
    validated_by_email  TEXT,        -- set on CONFIRMED / REJECTED rows
    rejection_reason    TEXT,

    -- Append metadata.
    ts                  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    submitted_by        TEXT NOT NULL
);

-- Composite "latest row per (op, day, person)" index.
CREATE INDEX IF NOT EXISTS attendance_key_idx
    ON v2.attendance (factory_id, shift, report_date, person_pan_hash, id DESC);
CREATE INDEX IF NOT EXISTS attendance_date_idx ON v2.attendance (report_date);
CREATE INDEX IF NOT EXISTS attendance_status_idx ON v2.attendance (status, ts DESC);
"""

CREATE_EVENTS = """
-- Catch-all for non-attendance things that happen during a shift.
-- The most common kind is DAILY_REPORT (the per-shift numerical report);
-- others include INCIDENT, NOTE, REMINDER_SENT.
CREATE TABLE IF NOT EXISTS v2.events (
    id                  BIGSERIAL PRIMARY KEY,

    kind                TEXT NOT NULL CHECK (kind IN
                          ('DAILY_REPORT','INCIDENT','NOTE','REMINDER_SENT','REMINDER_SKIPPED')),

    -- Composite key. Many event kinds are scoped to a shift on a date.
    factory_id          TEXT NOT NULL,
    shift               TEXT NOT NULL,
    report_date         DATE NOT NULL,

    -- Kind-specific body.
    --   DAILY_REPORT:  {chiefs, captains, operators, devices_deployed, devices_available,
    --                   devices_lost, devices_recovered, sd_cards_used, sd_cards_left,
    --                   actual_reporting_time, notes}
    --   INCIDENT:      {summary, severity, photo_url}
    --   NOTE:          {text}
    --   REMINDER_SENT: {chief_email, channel, message_id}
    payload             JSONB NOT NULL DEFAULT '{}'::jsonb,

    -- Append metadata.
    ts                  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    submitted_by        TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS events_dim_idx
    ON v2.events (factory_id, shift, report_date, kind, id DESC);
CREATE INDEX IF NOT EXISTS events_kind_date_idx ON v2.events (kind, report_date);
"""


# ---------------------------------------------------------------------------
# Append-only enforcement
# ---------------------------------------------------------------------------

CREATE_APPEND_ONLY_TRIGGER = """
CREATE OR REPLACE FUNCTION v2.raise_append_only() RETURNS trigger
    LANGUAGE plpgsql AS $$
BEGIN
    RAISE EXCEPTION 'append_only_violation: % on % is not allowed; INSERT a new row instead',
        TG_OP, TG_TABLE_NAME
        USING ERRCODE = '42501';   -- insufficient_privilege
END;
$$;
"""


def _trigger_for(table: str) -> str:
    """Create a BEFORE UPDATE OR DELETE trigger on `v2.<table>`."""
    return f"""
DROP TRIGGER IF EXISTS append_only ON v2.{table};
CREATE TRIGGER append_only
    BEFORE UPDATE OR DELETE ON v2.{table}
    FOR EACH ROW EXECUTE FUNCTION v2.raise_append_only();
"""


APPEND_ONLY_TRIGGERS = "\n".join(
    _trigger_for(t) for t in
    ("operators", "factories", "operations", "attendance", "events")
)


# ---------------------------------------------------------------------------
# Read views (transform-on-read)
# ---------------------------------------------------------------------------

CREATE_VIEWS = """
-- Latest row per stable key. All views live in the v2 schema.

CREATE OR REPLACE VIEW v2.v_operators_current AS
SELECT DISTINCT ON (pan_hash) *
  FROM v2.operators
 ORDER BY pan_hash, id DESC;

CREATE OR REPLACE VIEW v2.v_operators_active AS
SELECT * FROM v2.v_operators_current WHERE op_state = 'ACTIVE';

CREATE OR REPLACE VIEW v2.v_factories_current AS
SELECT DISTINCT ON (factory_id) *
  FROM v2.factories
 ORDER BY factory_id, id DESC;

CREATE OR REPLACE VIEW v2.v_factories_active AS
SELECT * FROM v2.v_factories_current WHERE state = 'ACTIVE';

CREATE OR REPLACE VIEW v2.v_operations_current AS
SELECT DISTINCT ON (operation_id) *
  FROM v2.operations
 ORDER BY operation_id, id DESC;

CREATE OR REPLACE VIEW v2.v_operations_active AS
SELECT * FROM v2.v_operations_current WHERE state = 'ACTIVE';

-- Roster: every active operator on every active operation, joined to
-- their canonical operator profile.
CREATE OR REPLACE VIEW v2.v_roster AS
SELECT
    op.factory_id,
    op.shift,
    op.role           AS deployed_role,
    op.operator_pan_hash,
    o.full_name,
    o.email,
    o.whatsapp,
    o.rank            AS operator_rank
  FROM v2.v_operations_active op
  JOIN v2.v_operators_current o ON o.pan_hash = op.operator_pan_hash;

-- Latest attendance row per (factory, shift, date, person).
CREATE OR REPLACE VIEW v2.v_attendance_current AS
SELECT DISTINCT ON (factory_id, shift, report_date, person_pan_hash) *
  FROM v2.attendance
 ORDER BY factory_id, shift, report_date, person_pan_hash, id DESC;

-- Latest daily-report row per (factory, shift, date).
CREATE OR REPLACE VIEW v2.v_daily_report_current AS
SELECT DISTINCT ON (factory_id, shift, report_date) *
  FROM v2.events
 WHERE kind = 'DAILY_REPORT'
 ORDER BY factory_id, shift, report_date, id DESC;

-- Aggregated view tuned for the dashboard: one row per active
-- (factory, shift), today, with attendance counts and the latest daily
-- report (if any). The API hits this and renders.
CREATE OR REPLACE VIEW v2.v_dashboard_today AS
WITH today AS (
    SELECT (CURRENT_DATE AT TIME ZONE 'Asia/Kolkata')::date AS d
),
deployments AS (
    -- Distinct (factory, shift) combos that currently have any active operator.
    SELECT DISTINCT factory_id, shift
      FROM v2.v_operations_active
),
att AS (
    SELECT
        factory_id, shift, report_date,
        COUNT(*)                                   AS attendance_count,
        COUNT(*) FILTER (WHERE status='CONFIRMED') AS confirmed_count,
        COUNT(*) FILTER (WHERE status='PENDING')   AS pending_count,
        COUNT(*) FILTER (WHERE status='REJECTED')  AS rejected_count
      FROM v2.v_attendance_current
     WHERE report_date = (SELECT d FROM today)
     GROUP BY factory_id, shift, report_date
)
SELECT
    f.factory_id,
    f.factory_name,
    f.location_link,
    d.shift,
    (SELECT d FROM today)               AS report_date,
    f.state                             AS factory_state,
    COALESCE(a.attendance_count, 0)     AS attendance_count,
    COALESCE(a.confirmed_count,  0)     AS confirmed_count,
    COALESCE(a.pending_count,    0)     AS pending_count,
    COALESCE(a.rejected_count,   0)     AS rejected_count,
    r.id                                AS daily_report_id,
    r.payload                           AS daily_report,
    r.submitted_by                      AS daily_submitted_by,
    r.ts                                AS daily_submitted_at
  FROM deployments d
  JOIN v2.v_factories_current f ON f.factory_id = d.factory_id
  LEFT JOIN att a            ON a.factory_id = d.factory_id AND a.shift = d.shift
  LEFT JOIN v2.v_daily_report_current r
                             ON r.factory_id = d.factory_id
                            AND r.shift      = d.shift
                            AND r.report_date = (SELECT d FROM today)
 WHERE f.state = 'ACTIVE';
"""


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

ALL_DDL = [
    CREATE_SCHEMA,
    CREATE_OPERATORS,
    CREATE_FACTORIES,
    CREATE_OPERATIONS,
    CREATE_ATTENDANCE,
    CREATE_EVENTS,
    CREATE_APPEND_ONLY_TRIGGER,
    APPEND_ONLY_TRIGGERS,
    CREATE_VIEWS,
]


async def init_db_v2(pool) -> None:
    """Idempotent v2 schema bootstrap. Safe to call on every boot — every
    statement uses IF NOT EXISTS / OR REPLACE.

    Does NOT touch the legacy v1 tables (submissions, operations as the
    old name, daily_reports, attendance, bot_roles, op_assignments,
    report_events, report_reminders). Migration to drop those happens in
    a separate, deliberate pass."""
    async with pool.acquire() as conn:
        for stmt in ALL_DDL:
            await conn.execute(stmt)


# ---------------------------------------------------------------------------
# Helpers shared by the v2 handlers (T step of ETL)
# ---------------------------------------------------------------------------

SHIFTS_BY_COUNT = {
    2: ("AM", "PM"),
    3: ("SHIFT A", "SHIFT B", "SHIFT C"),
}


def normalize_shift(shift_count: int, raw: str) -> str:
    """Validate + uppercase the shift string against the factory's shift_count.
    Raises ValueError if the shift is not one of the allowed values."""
    if shift_count not in SHIFTS_BY_COUNT:
        raise ValueError(f"shift_count must be 2 or 3, got {shift_count}")
    s = (raw or "").strip().upper()
    allowed = SHIFTS_BY_COUNT[shift_count]
    if s not in allowed:
        raise ValueError(f"shift must be one of {allowed} for a {shift_count}-shift factory")
    return s


def slugify_factory(factory_name: str) -> str:
    """Stable factory_id from factory_name. UPPERCASE input -> snake_case slug."""
    import re
    s = re.sub(r"[^A-Za-z0-9]+", "_", (factory_name or "").strip()).strip("_").lower()
    return s or "x"


def operation_id(factory_id: str, shift: str, pan_hash: str) -> str:
    """Stable operation_id used as the latest-row dedup key."""
    return f"{factory_id}__{shift.replace(' ', '_').lower()}__{pan_hash[:12]}"


# Approval chain: each row's validator_role is the rank one step above
# the person who submitted attendance. Operators are reviewed by captains,
# captains by chiefs, chiefs by generals. Generals + freddy don't submit
# attendance themselves (no row → no validator).
VALIDATOR_FOR = {
    "OPERATOR": "CAPTAIN",
    "CAPTAIN":  "CHIEF",
    "CHIEF":    "GENERAL",
}


def validator_for(person_role: str) -> str:
    """Return the role expected to validate this person's attendance.
    Raises KeyError for ranks that don't submit attendance."""
    return VALIDATOR_FOR[person_role.upper()]
