-- =============================================================================
-- Migration: LinkedIn Outreach State-Machine Columns
-- File     : sql/linkedin_outreach_columns.sql
-- Applies  : MySQL – run once against the prospects table
-- Safe     : ADD COLUMN IF NOT EXISTS (MySQL 8.0+); idempotent
-- =============================================================================

ALTER TABLE prospects
    ADD COLUMN IF NOT EXISTS outreach_required        TINYINT(1)  NOT NULL DEFAULT 0,
    ADD COLUMN IF NOT EXISTS outreach_sent            TINYINT(1)  NOT NULL DEFAULT 0,
    ADD COLUMN IF NOT EXISTS outreach_status          VARCHAR(50)           DEFAULT NULL,
    ADD COLUMN IF NOT EXISTS outreach_type            VARCHAR(50)           DEFAULT NULL,
    ADD COLUMN IF NOT EXISTS outreach_error           LONGTEXT              DEFAULT NULL,
    ADD COLUMN IF NOT EXISTS outreach_ts              DATETIME              DEFAULT NULL,
    ADD COLUMN IF NOT EXISTS outreach_attempts        INT         NOT NULL DEFAULT 0,
    ADD COLUMN IF NOT EXISTS outreach_last_attempt_ts DATETIME              DEFAULT NULL,
    ADD COLUMN IF NOT EXISTS outreach_in_progress     TINYINT(1)  NOT NULL DEFAULT 0;

-- Index for the fetch-query filter
CREATE INDEX IF NOT EXISTS ix_prospect_outreach_required
    ON prospects (outreach_required, outreach_sent, outreach_in_progress, outreach_status);

-- Release any locks stuck from a prior crashed run (>2 hours old)
UPDATE prospects
SET    outreach_in_progress = 0
WHERE  outreach_in_progress = 1
  AND  outreach_last_attempt_ts < DATE_SUB(UTC_TIMESTAMP(), INTERVAL 2 HOUR);

-- Backfill defaults
UPDATE prospects SET outreach_attempts = 0 WHERE outreach_attempts IS NULL;

SELECT 'linkedin_outreach_columns migration complete.' AS status;
