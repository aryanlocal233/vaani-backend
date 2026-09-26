-- Vaani Knowledge Pack schema. One deployment (VPS/domain) serves one active
-- event pack, selected via EVENT_PACK env var -- "Vaani App + Event Knowledge
-- Pack = Event Deployment". Adding a new event is a new pack's rows, not a
-- code change.

CREATE TABLE IF NOT EXISTS knowledge_packs (
    id          TEXT PRIMARY KEY,       -- e.g. 'gaya_ji'
    name        TEXT NOT NULL,
    active      BOOLEAN NOT NULL DEFAULT TRUE,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS faq_knowledge (
    pack_id         TEXT NOT NULL REFERENCES knowledge_packs(id),
    faq_id          TEXT NOT NULL,          -- stable slug, e.g. 'toilet'
    category        TEXT NOT NULL DEFAULT 'static',  -- static|location|time|emergency
    status          TEXT NOT NULL DEFAULT 'approved', -- draft|approved|retired
    version         INT NOT NULL DEFAULT 1,
    approved_by     TEXT,
    cache_policy    TEXT NOT NULL DEFAULT 'aggressive', -- aggressive|validate|ttl|no_cache
    valid_until     TIMESTAMPTZ,
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (pack_id, faq_id)
);

CREATE TABLE IF NOT EXISTS faq_keywords (
    pack_id     TEXT NOT NULL,
    faq_id      TEXT NOT NULL,
    language    TEXT NOT NULL,
    keyword     TEXT NOT NULL,
    FOREIGN KEY (pack_id, faq_id) REFERENCES faq_knowledge(pack_id, faq_id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_faq_keywords_lookup ON faq_keywords (pack_id, language);

CREATE TABLE IF NOT EXISTS faq_answers (
    pack_id     TEXT NOT NULL,
    faq_id      TEXT NOT NULL,
    language    TEXT NOT NULL,
    answer_text TEXT NOT NULL,
    version     INT NOT NULL DEFAULT 1,
    reviewed    BOOLEAN NOT NULL DEFAULT FALSE,
    updated_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (pack_id, faq_id, language),
    FOREIGN KEY (pack_id, faq_id) REFERENCES faq_knowledge(pack_id, faq_id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS audio_cache (
    pack_id     TEXT NOT NULL,
    faq_id      TEXT NOT NULL,
    language    TEXT NOT NULL,
    voice       TEXT NOT NULL,
    version     INT NOT NULL,
    audio_path  TEXT NOT NULL,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (pack_id, faq_id, language, voice, version),
    FOREIGN KEY (pack_id, faq_id) REFERENCES faq_knowledge(pack_id, faq_id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS admin_users (
    id              SERIAL PRIMARY KEY,
    username        TEXT UNIQUE NOT NULL,
    password_hash   TEXT NOT NULL,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS conversation_analytics (
    id                      BIGSERIAL PRIMARY KEY,
    ts                      TIMESTAMPTZ NOT NULL DEFAULT now(),
    pack_id                 TEXT NOT NULL,
    counter_id              TEXT NOT NULL DEFAULT 'unknown',
    detected_language       TEXT,
    faq_id                  TEXT,
    cache_hit               TEXT NOT NULL DEFAULT 'none',   -- 'faq' | 'none'
    translation_api_used    BOOLEAN NOT NULL DEFAULT FALSE,
    tts_api_used            BOOLEAN NOT NULL DEFAULT FALSE,
    stt_ms                  INT,
    response_ms             INT,
    escalated               BOOLEAN NOT NULL DEFAULT FALSE
);
CREATE INDEX IF NOT EXISTS idx_analytics_pack_ts ON conversation_analytics (pack_id, ts);
