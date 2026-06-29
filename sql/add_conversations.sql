-- ============================================================
-- Migration: LinkedIn Conversation Threading
-- Run once:  python init_db.py  OR  execute this SQL directly
-- ============================================================

CREATE TABLE IF NOT EXISTS linkedin_conversations (
    id                      INT AUTO_INCREMENT PRIMARY KEY,

    -- Link to the prospect this conversation belongs to
    prospect_id             INT NOT NULL,
    agent_id                INT,

    -- LinkedIn profile URL (denormalised for quick lookup without joining)
    linkedin_profile_url    VARCHAR(500) NOT NULL,

    -- Conversation state machine
    -- values: active | closed | not_interested | meeting_booked
    conversation_status     VARCHAR(50)  NOT NULL DEFAULT 'active',

    -- Lead qualification stage
    -- values: cold | warming | interested | hot | converted | dead
    lead_stage              VARCHAR(50)  NOT NULL DEFAULT 'cold',

    -- Full message thread stored as JSON array
    -- Each element: { "role": "us"|"them", "text": "...", "ts": "ISO8601" }
    thread_json             TEXT,

    -- Total message counts
    messages_sent           INT          NOT NULL DEFAULT 0,
    messages_received       INT          NOT NULL DEFAULT 0,

    -- Timestamps
    first_message_sent_utc  DATETIME,
    last_message_sent_utc   DATETIME,
    last_reply_received_utc DATETIME,
    last_checked_utc        DATETIME,

    -- AI-generated reply that is queued to send next (if any)
    pending_reply_text      TEXT,
    pending_reply_generated_utc DATETIME,

    -- Error tracking
    last_error              TEXT,
    error_count             INT          NOT NULL DEFAULT 0,

    created_at_utc          DATETIME     NOT NULL DEFAULT UTC_TIMESTAMP(),
    updated_at_utc          DATETIME     NOT NULL DEFAULT UTC_TIMESTAMP()
                                         ON UPDATE UTC_TIMESTAMP(),

    UNIQUE KEY uq_conv_prospect (prospect_id),
    INDEX ix_conv_agent (agent_id),
    INDEX ix_conv_status (conversation_status),
    INDEX ix_conv_last_checked (last_checked_utc),
    INDEX ix_conv_lead_stage (lead_stage)
);