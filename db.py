from __future__ import annotations
import csv
import secrets
import sqlite3
from pathlib import Path
from werkzeug.security import generate_password_hash

BASE_DIR = Path(__file__).parent
DB_PATH   = BASE_DIR / 'expenditure.db'
COMPANIES_CSV  = BASE_DIR / 'companies.csv'
CATEGORIES_CSV = BASE_DIR / 'categories.csv'

SCHEMA = """
CREATE TABLE IF NOT EXISTS app_config (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS users (
    username      TEXT PRIMARY KEY,
    password_hash TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS uploaded_batches (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    account     TEXT NOT NULL,
    uploaded_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    file_count  INTEGER,
    row_count   INTEGER
);

CREATE TABLE IF NOT EXISTS uploaded_files (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    batch_id    INTEGER REFERENCES uploaded_batches(id),
    filename    TEXT NOT NULL,
    file_size   INTEGER NOT NULL,
    uploaded_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_uf_name_size ON uploaded_files(filename, file_size);

CREATE TABLE IF NOT EXISTS transactions (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    date         TEXT NOT NULL,
    description  TEXT,
    account_name TEXT,
    value        REAL,
    source_file  TEXT,
    company      TEXT DEFAULT '__Unknown',
    category     TEXT DEFAULT '__Unknown',
    batch_id     INTEGER REFERENCES uploaded_batches(id)
);

CREATE TABLE IF NOT EXISTS company_map (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    description TEXT NOT NULL UNIQUE COLLATE NOCASE,
    company     TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS category_map (
    id       INTEGER PRIMARY KEY AUTOINCREMENT,
    company  TEXT NOT NULL UNIQUE COLLATE NOCASE,
    category TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS user_prefs (
    username   TEXT PRIMARY KEY,
    prefs_json TEXT NOT NULL DEFAULT '{}'
);

CREATE TABLE IF NOT EXISTS accounts (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    name         TEXT NOT NULL UNIQUE,
    reader_type  TEXT NOT NULL,
    reader_label TEXT
);

CREATE TABLE IF NOT EXISTS share_instruments (
    ticker     TEXT PRIMARY KEY,
    name       TEXT NOT NULL,
    currency   TEXT,
    kind       TEXT,
    role       TEXT NOT NULL DEFAULT 'watch',
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS share_prices (
    ticker    TEXT NOT NULL,
    date      TEXT NOT NULL,
    close     REAL NOT NULL,
    adj_close REAL,
    PRIMARY KEY (ticker, date)
);

CREATE TABLE IF NOT EXISTS share_trades (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    username   TEXT NOT NULL,
    trade_date TEXT NOT NULL,
    ticker     TEXT NOT NULL,
    side       TEXT NOT NULL,
    quantity   REAL NOT NULL,
    total_gbp  REAL NOT NULL,
    fees_gbp   REAL NOT NULL DEFAULT 0,
    platform   TEXT,
    account    TEXT,
    notes      TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS share_feeds (
    id    INTEGER PRIMARY KEY AUTOINCREMENT,
    name  TEXT NOT NULL,
    url   TEXT NOT NULL UNIQUE,
    topic TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS share_news (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    topic      TEXT NOT NULL,
    source     TEXT,
    title      TEXT NOT NULL,
    link       TEXT NOT NULL UNIQUE,
    published  TEXT,
    fetched_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS share_platforms (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    name        TEXT NOT NULL UNIQUE COLLATE NOCASE,
    holding_fee TEXT,
    dealing_fee TEXT,
    fx_fee      TEXT,
    accounts    TEXT,
    yearly_cost REAL,
    notes       TEXT,
    source_url  TEXT,
    checked_on  TEXT
);

CREATE TABLE IF NOT EXISTS share_facts (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    topic      TEXT NOT NULL UNIQUE COLLATE NOCASE,
    fact       TEXT NOT NULL,
    source_url TEXT,
    checked_on TEXT
);

CREATE INDEX IF NOT EXISTS idx_share_trades_user ON share_trades(username, trade_date);
CREATE INDEX IF NOT EXISTS idx_share_news_topic  ON share_news(topic, published DESC);
CREATE INDEX IF NOT EXISTS idx_tx_date     ON transactions(date);
CREATE INDEX IF NOT EXISTS idx_tx_company  ON transactions(company);
CREATE INDEX IF NOT EXISTS idx_tx_category ON transactions(category);
"""


def get_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def get_or_create_secret_key() -> str:
    conn = get_conn()
    row = conn.execute("SELECT value FROM app_config WHERE key='secret_key'").fetchone()
    if row:
        conn.close()
        return row['value']
    key = secrets.token_hex(32)
    conn.execute("INSERT INTO app_config (key, value) VALUES ('secret_key', ?)", (key,))
    conn.commit()
    conn.close()
    return key


def init_db() -> None:
    conn = get_conn()
    conn.executescript(SCHEMA)
    conn.commit()

    # Ensure secret key exists
    get_or_create_secret_key()

    # Default user — empty password_hash signals first-login password setup
    if not conn.execute("SELECT 1 FROM users WHERE username='tdrayson'").fetchone():
        conn.execute(
            "INSERT INTO users (username, password_hash) VALUES (?, ?)",
            ('tdrayson', '')
        )
        conn.commit()
        print("[db] Created default user 'tdrayson' (no password — will be set on first login)")

    # Seed accounts
    if conn.execute("SELECT COUNT(*) as n FROM accounts").fetchone()['n'] == 0:
        DEFAULT_ACCOUNTS = [
            ('Barclaycard 0% Credit Card', 'barclay', None),
            ('NatWest Current',            'natwest', 'Current'),
            ('NatWest 0% Credit Card',     'natwest', 'NatWest 0%'),
            ('NatWest Black Credit Card',  'natwest', 'Credit Card'),
            ('Tesco 0% Credit Card',       'tesco',   None),
            ('M&S 0% Credit Card',         'natwest', 'M&S 0% Credit Card'),
        ]
        conn.executemany(
            "INSERT INTO accounts (name, reader_type, reader_label) VALUES (?, ?, ?)",
            DEFAULT_ACCOUNTS
        )
        conn.commit()
        print("[db] Seeded default accounts")

    # Seed company_map from companies.csv
    if COMPANIES_CSV.exists() and conn.execute("SELECT COUNT(*) as n FROM company_map").fetchone()['n'] == 0:
        with open(COMPANIES_CSV, encoding='utf-8-sig') as f:
            rows = [
                (r['Description'].strip(), r['Company'].strip())
                for r in csv.DictReader(f)
                if r.get('Description', '').strip()
            ]
        conn.executemany("INSERT OR IGNORE INTO company_map (description, company) VALUES (?, ?)", rows)
        conn.commit()
        print(f"[db] Seeded {len(rows)} company mappings")

    # Seed category_map from categories.csv
    if CATEGORIES_CSV.exists() and conn.execute("SELECT COUNT(*) as n FROM category_map").fetchone()['n'] == 0:
        with open(CATEGORIES_CSV, encoding='utf-8-sig') as f:
            rows = [
                (r['Company'].strip(), r['Category'].strip())
                for r in csv.DictReader(f)
                if r.get('Company', '').strip()
            ]
        conn.executemany("INSERT OR IGNORE INTO category_map (company, category) VALUES (?, ?)", rows)
        conn.commit()
        print(f"[db] Seeded {len(rows)} category mappings")

    conn.close()
