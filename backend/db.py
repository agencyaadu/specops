import asyncpg
import os

CREATE_SUBMISSIONS = """
CREATE TABLE IF NOT EXISTS submissions (
    id                  BIGSERIAL PRIMARY KEY ,
    created_at          TIMESTAMPTZ DEFAULT NOW() ,

    -- identity
    full_name           TEXT NOT NULL ,
    whatsapp            TEXT NOT NULL ,
    email               TEXT NOT NULL ,
    alt_email           TEXT ,
    occupation          TEXT ,
    google_id           TEXT ,
    google_picture      TEXT ,

    -- socials
    telegram_id         TEXT ,
    discord_id          TEXT ,
    twitter_id          TEXT ,
    referred_by         TEXT ,

    -- languages
    languages           TEXT[] ,

    -- about
    hardest_problem     TEXT ,
    health_notes        TEXT ,

    -- address
    address_line1       TEXT ,
    address_line2       TEXT ,
    pincode             TEXT ,
    city                TEXT ,
    state               TEXT ,

    -- payment
    upi_id              TEXT ,
    beneficiary_name    TEXT ,
    account_number_enc  TEXT ,
    ifsc_code           TEXT ,
    bank_name           TEXT ,
    branch_name         TEXT ,

    -- pan
    pan_number_enc      TEXT ,

    -- files (Supabase Storage public URL for PAN; public URL for profile pic;
    -- optional public link for intro video)
    pan_card_url        TEXT ,
    profile_picture_url TEXT ,
    intro_video_url     TEXT ,

    -- consent
    consented           BOOLEAN NOT NULL DEFAULT FALSE ,
    consented_terms     BOOLEAN NOT NULL DEFAULT FALSE
);

ALTER TABLE submissions ADD COLUMN IF NOT EXISTS profile_picture_url TEXT;
CREATE INDEX IF NOT EXISTS idx_submissions_email      ON submissions(email);
CREATE INDEX IF NOT EXISTS idx_submissions_created_at ON submissions(created_at DESC);
"""

CREATE_OPERATIONS = """
CREATE TABLE IF NOT EXISTS operations (
    op_id                     TEXT PRIMARY KEY ,
    factory_name              TEXT NOT NULL ,
    shift                     TEXT NOT NULL ,
    location                  TEXT ,
    map_link                  TEXT ,
    whatsapp_group_url        TEXT ,
    poc1_name                 TEXT ,
    poc1_phone                TEXT ,
    poc1_role                 TEXT ,
    poc2_name                 TEXT ,
    poc2_phone                TEXT ,
    poc2_role                 TEXT ,
    sales_team_name           TEXT ,
    shift_start               TIME ,
    shift_end                 TIME ,
    reporting_time            TIME ,
    deployment_start          TIME ,
    collection_start          TIME ,
    report_submission_time    TIME ,
    final_closing_time        TIME ,
    is_active                 BOOLEAN DEFAULT TRUE ,
    created_at                TIMESTAMPTZ DEFAULT NOW() ,
    UNIQUE (factory_name, shift)
);
ALTER TABLE operations ADD COLUMN IF NOT EXISTS whatsapp_group_url TEXT;
"""

CREATE_REPORT_REMINDERS = """
CREATE TABLE IF NOT EXISTS report_reminders (
    id              BIGSERIAL PRIMARY KEY ,
    op_id           TEXT NOT NULL REFERENCES operations(op_id) ON DELETE CASCADE ,
    chief_email     TEXT NOT NULL ,
    report_date     DATE NOT NULL ,
    kind            TEXT NOT NULL DEFAULT 'discord' CHECK (kind IN ('discord','whatsapp','email')) ,
    status          TEXT NOT NULL DEFAULT 'sent' CHECK (status IN ('sent','failed','skipped')) ,
    error           TEXT ,
    sent_at         TIMESTAMPTZ NOT NULL DEFAULT NOW() ,
    sent_by_email   TEXT
);
CREATE INDEX IF NOT EXISTS idx_report_reminders_recent
    ON report_reminders(op_id, chief_email, sent_at DESC);
"""

CREATE_DAILY_REPORTS = """
CREATE TABLE IF NOT EXISTS daily_reports (
    id                      BIGSERIAL PRIMARY KEY ,
    op_id                   TEXT NOT NULL REFERENCES operations(op_id) ,
    report_date             DATE NOT NULL ,
    chiefs                  INT ,
    captains                INT ,
    operators               INT ,
    sd_cards_used           INT ,
    sd_cards_left           INT ,
    devices_available       INT ,
    devices_deployed        INT ,
    devices_lost            INT ,
    devices_recovered       INT ,
    actual_reporting_time   TIME ,
    submitted_by_email      TEXT ,
    submitted_at            TIMESTAMPTZ DEFAULT NOW() ,
    UNIQUE (op_id, report_date)
);
CREATE INDEX IF NOT EXISTS idx_daily_reports_date ON daily_reports(report_date);
"""

CREATE_REPORT_EVENTS = """
CREATE TABLE IF NOT EXISTS report_events (
    id          BIGSERIAL PRIMARY KEY ,
    report_id   BIGINT NOT NULL REFERENCES daily_reports(id) ON DELETE CASCADE ,
    ts          TIMESTAMPTZ NOT NULL ,
    note        TEXT NOT NULL
);
"""

CREATE_ATTENDANCE = """
CREATE TABLE IF NOT EXISTS attendance (
    id                BIGSERIAL PRIMARY KEY ,
    op_id             TEXT NOT NULL REFERENCES operations(op_id) ,
    report_date       DATE NOT NULL ,
    full_name         TEXT NOT NULL ,
    phone             TEXT NOT NULL ,
    person_role       TEXT NOT NULL CHECK (person_role IN ('chief','captain','operator')) ,
    pan_number_enc    TEXT NOT NULL ,
    pan_number_hash   TEXT NOT NULL ,
    photo_key         TEXT ,
    photo_exif_lat    NUMERIC(9, 6) ,
    photo_exif_lng    NUMERIC(9, 6) ,
    browser_lat       NUMERIC(9, 6) NOT NULL ,
    browser_lng       NUMERIC(9, 6) NOT NULL ,
    browser_accuracy_m NUMERIC ,
    distance_m        NUMERIC ,
    verified          BOOLEAN DEFAULT TRUE ,
    submitted_at      TIMESTAMPTZ DEFAULT NOW() ,
    submitted_by_email TEXT ,
    status            TEXT NOT NULL DEFAULT 'confirmed' CHECK (status IN ('pending','confirmed','rejected')) ,
    validator_role    TEXT CHECK (validator_role IN ('chief','captain')) ,
    confirmed_by_email TEXT ,
    confirmed_at      TIMESTAMPTZ ,
    rejected_by_email TEXT ,
    rejected_at       TIMESTAMPTZ ,
    reject_reason     TEXT ,
    UNIQUE (op_id, report_date, pan_number_hash)
);
ALTER TABLE attendance ALTER COLUMN photo_key DROP NOT NULL;
ALTER TABLE attendance ADD COLUMN IF NOT EXISTS browser_accuracy_m NUMERIC;
ALTER TABLE attendance ALTER COLUMN browser_lat SET NOT NULL;
ALTER TABLE attendance ALTER COLUMN browser_lng SET NOT NULL;
ALTER TABLE attendance ADD COLUMN IF NOT EXISTS submitted_by_email TEXT;
ALTER TABLE attendance ADD COLUMN IF NOT EXISTS status TEXT NOT NULL DEFAULT 'confirmed';
ALTER TABLE attendance DROP CONSTRAINT IF EXISTS attendance_status_check;
ALTER TABLE attendance ADD CONSTRAINT attendance_status_check CHECK (status IN ('pending','confirmed','rejected'));
ALTER TABLE attendance ADD COLUMN IF NOT EXISTS validator_role TEXT;
ALTER TABLE attendance DROP CONSTRAINT IF EXISTS attendance_validator_role_check;
ALTER TABLE attendance ADD CONSTRAINT attendance_validator_role_check CHECK (validator_role IS NULL OR validator_role IN ('chief','captain'));
ALTER TABLE attendance ADD COLUMN IF NOT EXISTS confirmed_by_email TEXT;
ALTER TABLE attendance ADD COLUMN IF NOT EXISTS confirmed_at TIMESTAMPTZ;
ALTER TABLE attendance ADD COLUMN IF NOT EXISTS rejected_by_email TEXT;
ALTER TABLE attendance ADD COLUMN IF NOT EXISTS rejected_at TIMESTAMPTZ;
ALTER TABLE attendance ADD COLUMN IF NOT EXISTS reject_reason TEXT;
CREATE INDEX IF NOT EXISTS idx_attendance_date ON attendance(report_date);
CREATE INDEX IF NOT EXISTS idx_attendance_pending ON attendance(op_id, status) WHERE status = 'pending';
"""

CREATE_BOT_ROLES = """
CREATE TABLE IF NOT EXISTS bot_roles (
    email           TEXT PRIMARY KEY ,
    role            TEXT NOT NULL CHECK (role IN ('freddy','general','chief','captain','viewer')) ,
    can_create_ops  BOOLEAN NOT NULL DEFAULT FALSE ,
    added_at        TIMESTAMPTZ DEFAULT NOW()
);
ALTER TABLE bot_roles ADD COLUMN IF NOT EXISTS can_create_ops BOOLEAN NOT NULL DEFAULT FALSE;
ALTER TABLE bot_roles DROP CONSTRAINT IF EXISTS bot_roles_role_check;
ALTER TABLE bot_roles ADD CONSTRAINT bot_roles_role_check CHECK (role IN ('freddy','general','chief','captain','viewer'));
"""

CREATE_NOTES = """
CREATE TABLE IF NOT EXISTS captain_notes (
    id          BIGSERIAL PRIMARY KEY ,
    email       TEXT NOT NULL ,
    role        TEXT ,
    op_id       TEXT REFERENCES operations(op_id) ON DELETE SET NULL ,
    kind        TEXT NOT NULL CHECK (kind IN ('journal','complaint','greeting','other')) ,
    note        TEXT NOT NULL ,
    created_at  TIMESTAMPTZ DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_captain_notes_created ON captain_notes(created_at DESC);
"""

CREATE_OP_ASSIGNMENTS = """
CREATE TABLE IF NOT EXISTS op_assignments (
    op_id             TEXT NOT NULL REFERENCES operations(op_id) ON DELETE CASCADE ,
    email             TEXT NOT NULL REFERENCES bot_roles(email) ON DELETE CASCADE ,
    role              TEXT NOT NULL CHECK (role IN ('chief','captain')) ,
    assigned_by_email TEXT ,
    added_at          TIMESTAMPTZ DEFAULT NOW() ,
    PRIMARY KEY (op_id, email)
);
CREATE INDEX IF NOT EXISTS idx_op_assignments_email ON op_assignments(email);
"""

ALL_DDL = [
    CREATE_SUBMISSIONS,
    CREATE_OPERATIONS,
    CREATE_DAILY_REPORTS,
    CREATE_REPORT_EVENTS,
    CREATE_BOT_ROLES,
    CREATE_ATTENDANCE,
    CREATE_OP_ASSIGNMENTS,
    CREATE_NOTES,
    CREATE_REPORT_REMINDERS,
]

async def init_db(pool: asyncpg.Pool):
    async with pool.acquire() as conn:
        for ddl in ALL_DDL:
            await conn.execute(ddl)
        await _seed_admins(conn)

async def _seed_admins(conn: asyncpg.Connection):
    # FREDDY_EMAILS seeds the top-tier role; MARSHAL_EMAILS / OWNER_EMAILS
    # remain accepted as legacy aliases (both upgrade to 'freddy').
    # GENERAL_EMAILS seeds second-tier admins.
    for env_var, role in (
        ("FREDDY_EMAILS",  "freddy"),
        ("MARSHAL_EMAILS", "freddy"),   # legacy alias
        ("OWNER_EMAILS",   "freddy"),   # legacy alias
        ("GENERAL_EMAILS", "general"),
    ):
        raw = os.environ.get(env_var, "").strip()
        if not raw:
            continue
        emails = [e.strip().lower() for e in raw.split(",") if e.strip()]
        for email in emails:
            await conn.execute(
                """
                INSERT INTO bot_roles (email, role) VALUES ($1, $2)
                ON CONFLICT (email) DO UPDATE SET
                    role = CASE
                        -- never downgrade an existing Freddy
                        WHEN bot_roles.role = 'freddy' THEN 'freddy'
                        WHEN EXCLUDED.role = 'freddy' THEN 'freddy'
                        ELSE EXCLUDED.role
                    END
                """,
                email, role,
            )
