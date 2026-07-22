"""SQLite storage. One file, no server."""
import sqlite3
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS slb_trades (
    date TEXT, symbol TEXT, series TEXT, rev_leg_date TEXT,
    prev_close REAL, open REAL, high REAL, low REAL, close REAL,
    qty INTEGER, value REAL, trades INTEGER,
    PRIMARY KEY (date, symbol, series)
);
CREATE TABLE IF NOT EXISTS slb_openpos (
    date TEXT, symbol TEXT, series TEXT, qty INTEGER,
    PRIMARY KEY (date, symbol, series)
);
CREATE TABLE IF NOT EXISTS prices (
    date TEXT, symbol TEXT, close REAL, prev_close REAL,
    delivery_pct REAL, traded_qty INTEGER,
    PRIMARY KEY (date, symbol)
);
CREATE TABLE IF NOT EXISTS futures (
    date TEXT, symbol TEXT, expiry TEXT,
    fut_close REAL, spot_close REAL, oi INTEGER,
    PRIMARY KEY (date, symbol, expiry)
);
CREATE TABLE IF NOT EXISTS fo_options (
    date TEXT, symbol TEXT, ce_oi INTEGER, pe_oi INTEGER,
    PRIMARY KEY (date, symbol)
);
CREATE TABLE IF NOT EXISTS fo_strikes (
    date TEXT, symbol TEXT, expiry TEXT, strike REAL, side TEXT, oi INTEGER,
    PRIMARY KEY (date, symbol, expiry, strike, side)
);
CREATE TABLE IF NOT EXISTS participant_oi (
    date TEXT, category TEXT, stk_fut_long INTEGER, stk_fut_short INTEGER,
    PRIMARY KEY (date, category)
);
CREATE TABLE IF NOT EXISTS signal_stats (
    direction TEXT, strength TEXT, n INTEGER, hits INTEGER, avg_ret REAL,
    PRIMARY KEY (direction, strength)
);
CREATE TABLE IF NOT EXISTS corp_actions (
    symbol TEXT, ex_date TEXT, subject TEXT,
    PRIMARY KEY (symbol, ex_date, subject)
);
CREATE TABLE IF NOT EXISTS scores (
    date TEXT, symbol TEXT, score INTEGER,
    fee_close REAL, ann_yield REAL, fee_trend REAL, vol_trend REAL,
    openpos_chg REAL, coc REAL, div_days INTEGER, reasons TEXT,
    pcr REAL, ma_trend TEXT,
    PRIMARY KEY (date, symbol)
);
CREATE TABLE IF NOT EXISTS watchlist (symbol TEXT PRIMARY KEY);
CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT);
CREATE INDEX IF NOT EXISTS idx_trades_sym ON slb_trades(symbol, date);
CREATE INDEX IF NOT EXISTS idx_scores_date ON scores(date, score);
"""


def connect(path: str | Path) -> sqlite3.Connection:
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(path)
    con.executescript(SCHEMA)
    return con


def has_date(con: sqlite3.Connection, table: str, d: str) -> bool:
    row = con.execute(f"SELECT 1 FROM {table} WHERE date=? LIMIT 1", (d,)).fetchone()
    return row is not None
