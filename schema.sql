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
    escalated               BOOLEAN NOT NULL DEFAULT FALSE,
    -- Real per-request cost, computed from Sarvam's published pay-as-you-go rates (pricing.py)
    -- at logging time -- not a post-hoc estimate. Kept as separate STT/translate/TTS columns
    -- (rather than one total) so the cost dashboard can show which stage actually costs money,
    -- and so "cost avoided by caching" can be computed as the translate+TTS a cache hit skipped.
    audio_duration_ms       INT NOT NULL DEFAULT 0,
    stt_cost_inr            NUMERIC(10,4) NOT NULL DEFAULT 0,
    translate_cost_inr      NUMERIC(10,4) NOT NULL DEFAULT 0,
    tts_cost_inr            NUMERIC(10,4) NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_analytics_pack_ts ON conversation_analytics (pack_id, ts);

-- General (non-FAQ) response cache: catches repeated conversational phrases -- especially the
-- operator's own recurring lines ("please wait", "next", "come this way") -- that aren't
-- FAQ-shaped but are still exact repeats. Exact-hash match only (not fuzzy, unlike FAQ matching)
-- -- a false match on arbitrary freeform conversation has no admin-curated safety net the way a
-- wrong FAQ match would, so this deliberately only fires on an exact normalized-text repeat.
CREATE TABLE IF NOT EXISTS translation_cache (
    pack_id         TEXT NOT NULL,
    src_lang        TEXT NOT NULL,
    tgt_lang        TEXT NOT NULL,
    text_hash       TEXT NOT NULL,      -- sha256 of the normalized source text
    source_text     TEXT NOT NULL,      -- kept for admin visibility/debugging only
    translated_text TEXT NOT NULL,
    hit_count       INT NOT NULL DEFAULT 1,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    last_used_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (pack_id, src_lang, tgt_lang, text_hash)
);

CREATE TABLE IF NOT EXISTS tts_audio_cache (
    pack_id     TEXT NOT NULL,
    lang        TEXT NOT NULL,
    voice       TEXT NOT NULL,
    text_hash   TEXT NOT NULL,          -- sha256 of the normalized text that was synthesized
    audio_path  TEXT NOT NULL,
    hit_count   INT NOT NULL DEFAULT 1,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    last_used_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (pack_id, lang, voice, text_hash)
);
