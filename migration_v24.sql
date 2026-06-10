-- v24: Branded-content (Tier-2) candidate set.
--
-- "Editorial" sponsorship — a sponsor is the reason for / featured in
-- the video (e.g. "Penalty Challenge by Audi", Pepsi UCL ceremony) —
-- as opposed to AMBIENT shirt/stadium sponsorship that's on every
-- clip. We seed candidates from two signals (scripts/scan_branded_
-- content.py):
--   1. videos.has_paid_promotion (YouTube's creator-disclosed flag).
--   2. Explicit disclosure phrases in title/description, multilingual
--      (EN/ES/IT/DE/FR/PT + KO/JA/AR keywords) — catches the UNTAGGED
--      branded content the flag misses.
--
-- High precision, not high recall: this is a candidate set we clean
-- over multiple passes via reviewed / is_branded. brand_guess is a
-- best-effort capture of the "by X" sponsor (secondary).

CREATE TABLE IF NOT EXISTS branded_content_candidates (
    video_id       uuid PRIMARY KEY REFERENCES videos(id) ON DELETE CASCADE,
    channel_id     uuid,
    has_paid_flag  boolean NOT NULL DEFAULT false,
    signals        text[]  NOT NULL DEFAULT '{}',
    brand_guess    text,            -- raw "by X" capture from the scanner
    brand_norm     text,            -- normalized for grouping (pass 2)
    score          int     NOT NULL DEFAULT 0,
    lang           text,
    reviewed       boolean NOT NULL DEFAULT false,
    is_branded     boolean,
    created_at     timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_bcc_channel  ON branded_content_candidates (channel_id);
CREATE INDEX IF NOT EXISTS idx_bcc_score    ON branded_content_candidates (score DESC);
CREATE INDEX IF NOT EXISTS idx_bcc_reviewed ON branded_content_candidates (reviewed, is_branded);
