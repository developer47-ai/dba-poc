-- Runs once, the first time the Postgres volume is created.
CREATE EXTENSION IF NOT EXISTS vector;     -- embeddings + similarity search
CREATE EXTENSION IF NOT EXISTS pg_trgm;    -- fuzzy text matching
CREATE EXTENSION IF NOT EXISTS unaccent;   -- accent-insensitive keyword search

-- One schema per concern; later steps create the tables.
CREATE SCHEMA IF NOT EXISTS kb;        -- SOP documents, chunks, steps, diagnostic codes
CREATE SCHEMA IF NOT EXISTS agent;     -- sessions, events, consent, checkpoints
CREATE SCHEMA IF NOT EXISTS memory;    -- long-term customer memory
