import asyncpg

CREATE_TABLE = """
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

    -- files (Supabase Storage public URLs)
    pan_card_url        TEXT ,
    intro_video_url     TEXT ,

    -- consent
    consented           BOOLEAN NOT NULL DEFAULT FALSE ,
    consented_terms     BOOLEAN NOT NULL DEFAULT FALSE
);

CREATE INDEX IF NOT EXISTS idx_submissions_email      ON submissions(email);
CREATE INDEX IF NOT EXISTS idx_submissions_created_at ON submissions(created_at DESC);
"""

async def init_db(pool: asyncpg.Pool):
    async with pool.acquire() as conn:
        await conn.execute(CREATE_TABLE)
