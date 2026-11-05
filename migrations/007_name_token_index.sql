-- Name and word indexes for the v2 matcher. Rebuilt with watchlist_entities on
-- each refresh, in the same transaction, so all describe one snapshot.
CREATE EXTENSION IF NOT EXISTS pg_trgm;
CREATE TABLE IF NOT EXISTS watchlist_tokens (
    token text PRIMARY KEY,
    doc_frequency integer NOT NULL
);
CREATE TABLE IF NOT EXISTS watchlist_name_tokens (
    token text NOT NULL,
    entity_kind text NOT NULL CHECK (entity_kind IN ('person','organization')),
    entity_id text NOT NULL,
    PRIMARY KEY (token, entity_kind, entity_id)
);
CREATE TABLE IF NOT EXISTS watchlist_names (
    name_norm text NOT NULL,
    entity_kind text NOT NULL CHECK (entity_kind IN ('person','organization')),
    entity_id text NOT NULL,
    PRIMARY KEY (name_norm, entity_kind, entity_id)
);
CREATE INDEX IF NOT EXISTS watchlist_names_trgm_idx ON watchlist_names USING gin(name_norm gin_trgm_ops);
CREATE INDEX IF NOT EXISTS watchlist_tokens_trgm_idx ON watchlist_tokens USING gin(token gin_trgm_ops);
CREATE INDEX IF NOT EXISTS watchlist_tokens_prefix_idx ON watchlist_tokens(left(token, 1), length(token));
