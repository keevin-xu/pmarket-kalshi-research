-- Point-in-time store schema. All timestamps are fixed-width ISO-8601 UTC
-- (see config.TS_FORMAT) so string ordering == chronological ordering.
-- Every market row carries provenance (source) and venue.

PRAGMA journal_mode=WAL;
PRAGMA foreign_keys=ON;

-- Canonical matches, keyed independently of any venue, tied to the NEUTRAL
-- results source. This is the join spine for coverage + outcomes.
CREATE TABLE IF NOT EXISTS matches (
    match_id        TEXT PRIMARY KEY,      -- our stable id (from neutral source)
    league          TEXT NOT NULL,         -- normalized tier-1 league code
    team_a          TEXT NOT NULL,
    team_b          TEXT NOT NULL,
    start_ts        TEXT NOT NULL,         -- scheduled/actual map/series start (kickoff)
    best_of         INTEGER,               -- series length
    neutral_source  TEXT NOT NULL,         -- 'oracles_elixir' | 'pandascore'
    result_winner   TEXT,                  -- team name that won (NULL until resolved)
    result_ts       TEXT                   -- when result became known (neutral)
);

-- Contracts on each venue, mapped to a canonical match + family.
-- parity_ok is set by parity/settlement.py; NULL = not yet evaluated.
CREATE TABLE IF NOT EXISTS contracts (
    contract_id     TEXT PRIMARY KEY,      -- venue-native id (condition id / ticker)
    venue           TEXT NOT NULL,         -- 'polymarket' | 'kalshi'
    match_id        TEXT REFERENCES matches(match_id),
    family          TEXT NOT NULL,         -- 'series_winner' | 'map_winner' | ...
    outcome_side    TEXT NOT NULL,         -- which team this YES leg pays
    question_text   TEXT,
    parity_ok       INTEGER,               -- 1 pass / 0 fail / NULL unevaluated
    UNIQUE(venue, contract_id)
);

-- Price/book snapshots. One row per (contract, ts). Gaps are information;
-- a one-sided book stores NULL for the missing side, never a zero.
CREATE TABLE IF NOT EXISTS quotes (
    contract_id     TEXT NOT NULL REFERENCES contracts(contract_id),
    venue           TEXT NOT NULL,
    ts              TEXT NOT NULL,         -- fixed-width ISO-8601 UTC
    source          TEXT NOT NULL,         -- 'hist' | 'live'
    regime          TEXT,                  -- 'pre_match' | 'in_game' | NULL if unknown
    bid             REAL,                  -- best bid (order book) or NULL
    ask             REAL,                  -- best ask or NULL
    mid             REAL,                  -- (bid+ask)/2 when both present
    last            REAL,
    bid_size_usd    REAL,                  -- top-of-book depth, this side
    ask_size_usd    REAL,
    PRIMARY KEY (contract_id, ts, source)
);
CREATE INDEX IF NOT EXISTS idx_quotes_ts ON quotes(ts);
CREATE INDEX IF NOT EXISTS idx_quotes_contract_ts ON quotes(contract_id, ts);

-- Stream cursors for live ingestion; committed atomically with the rows
-- they cover so a restart cannot re-process old data.
CREATE TABLE IF NOT EXISTS cursors (
    stream          TEXT PRIMARY KEY,      -- e.g. 'kalshi:orderbook'
    cursor_value    TEXT NOT NULL,
    updated_ts      TEXT NOT NULL
);

-- Every discarded evaluation is stored with a reason code. The discard
-- distribution is a first-class research output.
CREATE TABLE IF NOT EXISTS discards (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    stage           TEXT NOT NULL,         -- 'census' | 'parity' | 'calibration' | 'lead_lag'
    contract_id     TEXT,
    match_id        TEXT,
    ts              TEXT NOT NULL,
    reason          TEXT NOT NULL          -- 'no_quote' | 'inside_spread' | 'below_depth'
                                           -- | 'failed_parity' | 'reference_unvalidated'
                                           -- | 'missing_outcome' | ...
);

-- Run artifacts index: every reported number traces to a stored artifact.
CREATE TABLE IF NOT EXISTS run_artifacts (
    run_id          TEXT NOT NULL,
    gate            TEXT NOT NULL,         -- 'G0'..'G4'
    created_ts      TEXT NOT NULL,
    path            TEXT NOT NULL,         -- file under data/artifacts/
    table_hash      TEXT,                  -- for determinism verification
    PRIMARY KEY (run_id, gate)
);
