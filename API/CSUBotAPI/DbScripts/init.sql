CREATE TABLE IF NOT EXISTS users (
    telegram_id BIGINT PRIMARY KEY,
    created_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS themes (
    id SERIAL PRIMARY KEY,
    user_id BIGINT NOT NULL REFERENCES users(telegram_id) ON DELETE CASCADE,
    name TEXT NOT NULL,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    UNIQUE(user_id, name)
);

CREATE TABLE IF NOT EXISTS words (
    id SERIAL PRIMARY KEY,
    theme_id INT NOT NULL REFERENCES themes(id) ON DELETE CASCADE,
    word TEXT NOT NULL,
    translation TEXT,
    access_count INT NOT NULL DEFAULT 0,
    definition TEXT,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    UNIQUE(theme_id, word)
);