"""
Share dealings: free daily prices, holdings, signals, outlook and news.

Prices come from Yahoo Finance's public chart endpoint (no key needed) and
news from RSS feeds. Everything is stored in the app database so pages load
from local data and only the refresh job touches the network.

Run `python shares.py refresh` to update prices and news from a scheduler.
"""
from __future__ import annotations

import json
import sys
import threading
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timedelta, timezone
from email.utils import parsedate_to_datetime

import numpy as np
import pandas as pd

import db

YAHOO_CHART  = 'https://query1.finance.yahoo.com/v8/finance/chart/'
YAHOO_SEARCH = 'https://query2.finance.yahoo.com/v1/finance/search'
GOOGLE_NEWS  = 'https://news.google.com/rss/search?hl=en-GB&gl=GB&ceid=GB:en&q='
_HEADERS = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 '
                  '(KHTML, like Gecko) Chrome/126.0 Safari/537.36',
}

TRADING_DAYS   = 252
NEWS_KEEP_DAYS = 45
ACCOUNT_TYPES  = ['Stocks & Shares ISA', 'General Account', 'SIPP']

DEFAULT_SETTINGS = {
    'total_savings': 90000.0,
    'reserve':       40000.0,
    'target_pct':    20.0,
    'core_pct':      70.0,
}

# (ticker, name, kind, role). 'watch' rows can be bought and get a signal;
# 'market' rows are indexes and rates shown for background only.
DEFAULT_INSTRUMENTS = [
    ('VWRP.L',   'Vanguard FTSE All-World (Acc)',         'ETF',   'watch'),
    ('VUAG.L',   'Vanguard S&P 500 (Acc)',                'ETF',   'watch'),
    ('SWDA.L',   'iShares Core MSCI World (Acc)',         'ETF',   'watch'),
    ('EQQQ.L',   'Invesco Nasdaq-100',                    'ETF',   'watch'),
    ('VUKG.L',   'Vanguard FTSE 100 (Acc)',               'ETF',   'watch'),
    ('VFEG.L',   'Vanguard FTSE Emerging Markets (Acc)',  'ETF',   'watch'),
    ('SGLN.L',   'iShares Physical Gold',                 'ETF',   'watch'),
    ('DFNG.L',   'VanEck Defense',                        'ETF',   'watch'),
    ('SMT.L',    'Scottish Mortgage Investment Trust',    'Trust', 'watch'),
    ('^FTSE',    'FTSE 100',                              'Index', 'market'),
    ('^FTMC',    'FTSE 250',                              'Index', 'market'),
    ('^GSPC',    'S&P 500',                               'Index', 'market'),
    ('^IXIC',    'Nasdaq Composite',                      'Index', 'market'),
    ('^VIX',     'VIX (US Market Fear Gauge)',            'Index', 'market'),
    ('^TNX',     'US 10-Year Bond Yield (%)',             'Rate',  'market'),
    ('GBPUSD=X', 'Pound To US Dollar',                    'FX',    'market'),
    ('GBPEUR=X', 'Pound To Euro',                         'FX',    'market'),
    ('BZ=F',     'Brent Crude Oil ($)',                   'Commodity', 'market'),
    ('GC=F',     'Gold ($ Per Ounce)',                    'Commodity', 'market'),
]

# (name, url, topic)
DEFAULT_FEEDS = [
    ('FT Markets',                 'https://www.ft.com/markets?format=rss', 'Markets'),
    ('Google News: Stock Markets', GOOGLE_NEWS + 'stock+market+shares+when:3d', 'Markets'),
    ('Google News: Forecasts',     GOOGLE_NEWS + 'stock+market+forecast+OR+outlook+OR+prediction+when:14d', 'Forecasts'),
    ('Google News: Analyst Views', GOOGLE_NEWS + 'analysts+shares+%22price+target%22+when:3d', 'Forecasts'),
    ('BBC Business',               'https://feeds.bbci.co.uk/news/business/rss.xml', 'Economy'),
    ('Guardian Economics',         'https://www.theguardian.com/business/economics/rss', 'Economy'),
    ('Bank Of England',            'https://www.bankofengland.co.uk/rss/news', 'Economy'),
    ('Google News: Rates & Inflation', GOOGLE_NEWS + 'interest+rates+inflation+UK+economy+when:7d', 'Economy'),
    ('BBC Politics',               'https://feeds.bbci.co.uk/news/politics/rss.xml', 'Politics'),
    ('Google News: Tax & ISAs',    GOOGLE_NEWS + 'budget+ISA+OR+%22capital+gains+tax%22+investors+when:14d', 'Politics'),
    ('BBC World',                  'https://feeds.bbci.co.uk/news/world/rss.xml', 'World & Conflict'),
    ('Google News: War & Markets', GOOGLE_NEWS + 'war+OR+sanctions+OR+tariffs+markets+oil+when:7d', 'World & Conflict'),
]

HOLDINGS_TOPIC = 'My Holdings'


# ---------------------------------------------------------------------------
# Settings and first-run data
# ---------------------------------------------------------------------------

def get_settings() -> dict:
    conn = db.get_conn()
    row = conn.execute("SELECT value FROM app_config WHERE key='shares_settings'").fetchone()
    conn.close()
    settings = dict(DEFAULT_SETTINGS)
    if row:
        settings.update(json.loads(row['value']))
    return settings


def save_settings(settings: dict) -> None:
    conn = db.get_conn()
    conn.execute(
        "INSERT OR REPLACE INTO app_config (key, value) VALUES ('shares_settings', ?)",
        (json.dumps(settings),)
    )
    conn.commit()
    conn.close()


def _config_get(conn, key: str) -> str | None:
    row = conn.execute('SELECT value FROM app_config WHERE key=?', (key,)).fetchone()
    return row['value'] if row else None


def init() -> None:
    """Seed the watchlist, news feeds, platforms and key facts on first run."""
    import shares_seed  # kept apart: long lists of text, only needed once

    conn = db.get_conn()
    if not conn.execute('SELECT 1 FROM share_instruments LIMIT 1').fetchone():
        conn.executemany(
            'INSERT INTO share_instruments (ticker, name, kind, role) VALUES (?, ?, ?, ?)',
            DEFAULT_INSTRUMENTS
        )
    if not conn.execute('SELECT 1 FROM share_feeds LIMIT 1').fetchone():
        conn.executemany(
            'INSERT INTO share_feeds (name, url, topic) VALUES (?, ?, ?)', DEFAULT_FEEDS
        )
    if not conn.execute('SELECT 1 FROM share_platforms LIMIT 1').fetchone():
        conn.executemany(
            '''INSERT INTO share_platforms
               (name, holding_fee, dealing_fee, fx_fee, accounts, yearly_cost,
                notes, source_url, checked_on)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)''',
            shares_seed.PLATFORMS
        )
    if not conn.execute('SELECT 1 FROM share_facts LIMIT 1').fetchone():
        conn.executemany(
            'INSERT INTO share_facts (topic, fact, source_url, checked_on) VALUES (?, ?, ?, ?)',
            shares_seed.FACTS
        )
    conn.commit()
    conn.close()


# ---------------------------------------------------------------------------
# Network: prices, ticker search, news
# ---------------------------------------------------------------------------

def _http_get(url: str, timeout: int = 20) -> bytes:
    req = urllib.request.Request(url, headers=_HEADERS)
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.read()


def fetch_history(ticker: str, span: str = '10y') -> tuple[dict, list[tuple]]:
    """
    Daily closes for one ticker.

    Returns (meta, rows) where rows are (date, close, adj_close) in the
    currency Yahoo quotes it in. London shares are often quoted in pence
    (currency 'GBp'), which is converted to pounds when valuing, not here.
    """
    url = (f'{YAHOO_CHART}{urllib.parse.quote(ticker)}'
           f'?range={span}&interval=1d&events=div%2Csplit')
    result = json.loads(_http_get(url))['chart']['result'][0]
    meta = result['meta']
    stamps = result.get('timestamp') or []
    closes = result['indicators']['quote'][0]['close']
    adj = (result['indicators'].get('adjclose') or [{}])[0].get('adjclose') or closes
    offset = meta.get('gmtoffset') or 0

    rows: list[tuple] = []
    prev = None
    for ts, c, a in zip(stamps, closes, adj):
        if c is None:
            continue
        a = c if a is None else a
        # Yahoo occasionally returns a London price in pounds on a day when
        # the rest of the history is in pence (or the reverse). Put it back
        # on the same scale as the day before.
        if prev:
            ratio = c / prev
            if ratio > 50:
                c, a = c / 100, a / 100
            elif ratio < 0.02:
                c, a = c * 100, a * 100
        prev = c
        day = datetime.fromtimestamp(ts + offset, tz=timezone.utc).date().isoformat()
        rows.append((day, float(c), float(a)))
    return meta, rows


def search_tickers(query: str) -> list[dict]:
    url = (f'{YAHOO_SEARCH}?q={urllib.parse.quote(query)}'
           '&quotesCount=10&newsCount=0')
    quotes = json.loads(_http_get(url, timeout=10)).get('quotes', [])
    return [
        {
            'ticker':   q.get('symbol'),
            'name':     q.get('longname') or q.get('shortname') or q.get('symbol'),
            'exchange': q.get('exchDisp') or q.get('exchange') or '',
            'kind':     q.get('typeDisp') or q.get('quoteType') or '',
        }
        for q in quotes if q.get('symbol')
    ]


def _parse_feed(xml_bytes: bytes) -> list[dict]:
    root = ET.fromstring(xml_bytes)
    atom = '{http://www.w3.org/2005/Atom}'
    items = []
    for it in root.iter('item'):
        items.append({
            'title':     (it.findtext('title') or '').strip(),
            'link':      (it.findtext('link') or '').strip(),
            'published': it.findtext('pubDate'),
            'source':    (it.findtext('source') or '').strip() or None,
        })
    for it in root.iter(f'{atom}entry'):
        link = it.find(f'{atom}link')
        items.append({
            'title':     (it.findtext(f'{atom}title') or '').strip(),
            'link':      link.get('href', '') if link is not None else '',
            'published': it.findtext(f'{atom}updated'),
            'source':    None,
        })

    out = []
    for item in items:
        # Only plain web links are stored, as they are shown as clickable links.
        if not item['title'] or not item['link'].startswith(('http://', 'https://')):
            continue
        item['published'] = _parse_when(item['published'])
        out.append(item)
    return out


def _parse_when(text: str | None) -> str | None:
    if not text:
        return None
    try:
        when = parsedate_to_datetime(text)
    except (TypeError, ValueError):
        try:
            when = datetime.fromisoformat(text.replace('Z', '+00:00'))
        except ValueError:
            return None
    if when.tzinfo is None:
        when = when.replace(tzinfo=timezone.utc)
    return when.astimezone(timezone.utc).strftime('%Y-%m-%d %H:%M')


# ---------------------------------------------------------------------------
# Refresh job
# ---------------------------------------------------------------------------

# Refreshing takes several seconds per source, so it runs on a worker thread
# and the page polls for progress.
_refresh: dict = {'running': False, 'stage': '', 'done': 0, 'total': 0, 'errors': [], 'log': []}
_refresh_lock = threading.Lock()
REFRESH_LOG = db.BASE_DIR / 'shares_refresh.log'


def _log(text: str) -> None:
    """Add a time-stamped line to the on-page log and to shares_refresh.log."""
    line = f'{datetime.now().strftime("%H:%M:%S")}  {text}'
    with _refresh_lock:
        _refresh['log'].append(line)
    try:
        with open(REFRESH_LOG, 'a', encoding='utf-8') as fh:
            fh.write(f'{datetime.now().strftime("%Y-%m-%d")} {line}\n')
    except OSError:
        pass


def refresh_status() -> dict:
    with _refresh_lock:
        return dict(_refresh, errors=list(_refresh['errors']), log=list(_refresh['log']))


def needs_refresh() -> bool:
    conn = db.get_conn()
    last = _config_get(conn, 'shares_last_refresh')
    conn.close()
    return not last or last[:10] < date.today().isoformat()


def start_refresh() -> bool:
    """Start a background refresh. Returns False if one is already running."""
    with _refresh_lock:
        if _refresh['running']:
            return False
        _refresh.update(running=True, stage='Starting', done=0, total=0, errors=[], log=[])
    threading.Thread(target=refresh_all, daemon=True).start()
    return True


def _progress(**fields) -> None:
    with _refresh_lock:
        _refresh.update(fields)


def _note_error(text: str) -> None:
    with _refresh_lock:
        _refresh['errors'].append(text)


def _ensure_fx(conn, currency: str | None) -> None:
    """Make sure the pound exchange rate for a share's currency is tracked."""
    if not currency or currency in ('GBP', 'GBp', 'GBX'):
        return
    pair = f'GBP{currency}=X'
    conn.execute(
        "INSERT OR IGNORE INTO share_instruments (ticker, name, currency, kind, role) "
        "VALUES (?, ?, ?, 'FX', 'market')",
        (pair, f'Pound To {currency}', currency)
    )


def _store_history(conn, ticker: str, meta: dict, rows: list[tuple]) -> None:
    conn.executemany(
        'INSERT OR REPLACE INTO share_prices (ticker, date, close, adj_close) VALUES (?, ?, ?, ?)',
        [(ticker, d, c, a) for d, c, a in rows]
    )
    conn.execute('UPDATE share_instruments SET currency=? WHERE ticker=?',
                 (meta.get('currency'), ticker))


def refresh_all() -> None:
    """Fetch prices for every tracked ticker, then every news feed."""
    try:
        _log('Refresh started')
        conn = db.get_conn()
        tickers = [r['ticker'] for r in conn.execute('SELECT ticker FROM share_instruments')]
        _progress(stage='Getting Prices', done=0, total=len(tickers))
        _log(f'Getting prices for {len(tickers)} tickers')

        def load(ticker):
            started = datetime.now()
            try:
                result = fetch_history(ticker)
                _log(f'Price OK: {ticker} ({len(result[1])} days, '
                     f'{(datetime.now() - started).total_seconds():.1f}s)')
                return ticker, result, None
            except Exception as exc:  # noqa: BLE001 - one bad ticker must not stop the rest
                _log(f'Price FAILED: {ticker} after '
                     f'{(datetime.now() - started).total_seconds():.1f}s - {exc}')
                return ticker, None, str(exc)

        done = 0
        with ThreadPoolExecutor(max_workers=4) as pool:
            for ticker, result, error in pool.map(load, tickers):
                done += 1
                _progress(done=done)
                if error or not result[1]:
                    _note_error(f'{ticker}: {error or "no prices returned"}')
                    continue
                _store_history(conn, ticker, *result)
                _ensure_fx(conn, result[0].get('currency'))
        conn.commit()

        # An exchange rate added just now has no prices yet.
        missing = [r['ticker'] for r in conn.execute(
            '''SELECT i.ticker FROM share_instruments i
               WHERE NOT EXISTS (SELECT 1 FROM share_prices p WHERE p.ticker = i.ticker)'''
        ) if r['ticker'] not in {e.split(':')[0] for e in refresh_status()['errors']}]
        for ticker in missing:
            _, result, error = load(ticker)
            if result and result[1]:
                _store_history(conn, ticker, *result)
        conn.commit()

        _refresh_news(conn)

        conn.execute(
            "INSERT OR REPLACE INTO app_config (key, value) VALUES ('shares_last_refresh', ?)",
            (datetime.now().strftime('%Y-%m-%d %H:%M'),)
        )
        conn.commit()
        conn.close()
        _progress(stage='Done')
        _log('Refresh finished')
    except Exception as exc:  # noqa: BLE001 - report any failure to the poller
        _note_error(f'Refresh failed: {exc}')
        _log(f'Refresh FAILED: {exc}')
    finally:
        _progress(running=False)


def _refresh_news(conn) -> None:
    feeds = [(r['name'], r['url'], r['topic'])
             for r in conn.execute('SELECT name, url, topic FROM share_feeds')]

    # One extra search per share currently held, so news on what is owned
    # shows up without a feed having to be set up for it.
    held = conn.execute(
        '''SELECT i.name FROM share_instruments i
           WHERE i.ticker IN (
               SELECT ticker FROM share_trades GROUP BY ticker
               HAVING SUM(CASE WHEN side='BUY' THEN quantity ELSE -quantity END) > 0.000001)'''
    ).fetchall()
    for r in held:
        query = urllib.parse.quote(f'"{r["name"]}" when:7d')
        feeds.append((f'Google News: {r["name"]}', GOOGLE_NEWS + query, HOLDINGS_TOPIC))

    _progress(stage='Getting News', done=0, total=len(feeds))
    _log(f'Getting news from {len(feeds)} feeds')

    def load(feed):
        name, url, topic = feed
        started = datetime.now()
        try:
            items = _parse_feed(_http_get(url))
            _log(f'News OK: {name} ({len(items)} items, '
                 f'{(datetime.now() - started).total_seconds():.1f}s)')
            return name, topic, items, None
        except Exception as exc:  # noqa: BLE001
            _log(f'News FAILED: {name} after '
                 f'{(datetime.now() - started).total_seconds():.1f}s - {exc}')
            return name, topic, [], str(exc)

    done = 0
    with ThreadPoolExecutor(max_workers=4) as pool:
        for name, topic, items, error in pool.map(load, feeds):
            done += 1
            _progress(done=done)
            if error:
                _note_error(f'{name}: {error}')
                continue
            conn.executemany(
                '''INSERT OR IGNORE INTO share_news (topic, source, title, link, published)
                   VALUES (?, ?, ?, ?, ?)''',
                [(topic, it['source'] or name, it['title'][:400], it['link'], it['published'])
                 for it in items[:40]]
            )
    cutoff = (datetime.now(timezone.utc) - timedelta(days=NEWS_KEEP_DAYS)).strftime('%Y-%m-%d %H:%M')
    conn.execute('DELETE FROM share_news WHERE COALESCE(published, fetched_at) < ?', (cutoff,))
    conn.commit()


def add_instrument(ticker: str, name: str = '', kind: str = '') -> str:
    """Add a ticker to the watchlist and load its history. Returns its name."""
    ticker = ticker.strip().upper()
    meta, rows = fetch_history(ticker)
    if not rows:
        raise ValueError(f'No prices found for {ticker}.')
    name = name.strip() or meta.get('longName') or meta.get('shortName') or ticker
    kind = kind.strip() or (meta.get('instrumentType') or '').title()
    conn = db.get_conn()
    conn.execute(
        '''INSERT INTO share_instruments (ticker, name, kind, role) VALUES (?, ?, ?, 'watch')
           ON CONFLICT(ticker) DO UPDATE SET name=excluded.name, kind=excluded.kind, role='watch' ''',
        (ticker, name, kind)
    )
    _store_history(conn, ticker, meta, rows)
    currency = meta.get('currency')
    _ensure_fx(conn, currency)
    conn.commit()
    if currency and currency not in ('GBP', 'GBp', 'GBX'):
        pair = f'GBP{currency}=X'
        if not conn.execute('SELECT 1 FROM share_prices WHERE ticker=? LIMIT 1', (pair,)).fetchone():
            _store_history(conn, pair, *fetch_history(pair))
            conn.commit()
    conn.close()
    return name


# ---------------------------------------------------------------------------
# Loading stored prices
# ---------------------------------------------------------------------------

def _instruments(conn) -> dict[str, dict]:
    return {r['ticker']: dict(r) for r in conn.execute('SELECT * FROM share_instruments')}


def _price_frame(conn, adjusted: bool) -> pd.DataFrame:
    """Dates down, tickers across. Adjusted prices include dividends paid."""
    column = 'COALESCE(adj_close, close)' if adjusted else 'close'
    df = pd.read_sql_query(
        f'SELECT ticker, date, {column} AS px FROM share_prices', conn, parse_dates=['date']
    )
    if df.empty:
        return pd.DataFrame()
    return df.pivot(index='date', columns='ticker', values='px').sort_index()


def _in_pounds(frame: pd.DataFrame, instruments: dict[str, dict]) -> pd.DataFrame:
    """Convert each watchlist ticker's prices to pounds per share."""
    out = {}
    for ticker in frame.columns:
        info = instruments.get(ticker)
        if not info or info['role'] != 'watch':
            continue
        series = frame[ticker].dropna()
        currency = info['currency']
        if currency in ('GBp', 'GBX'):
            series = series / 100
        elif currency and currency != 'GBP':
            pair = f'GBP{currency}=X'
            if pair not in frame.columns:
                continue
            rate = frame[pair].ffill().reindex(series.index, method='ffill')
            series = (series / rate).dropna()
        out[ticker] = series
    return pd.DataFrame(out).sort_index() if out else pd.DataFrame()


# ---------------------------------------------------------------------------
# Holdings
# ---------------------------------------------------------------------------

def _trades(conn, username: str) -> list[dict]:
    return [dict(r) for r in conn.execute(
        'SELECT * FROM share_trades WHERE username=? ORDER BY trade_date, id', (username,)
    )]


def _pool(trades: list[dict]) -> tuple[dict[str, dict], float]:
    """
    Quantity and cost held per ticker, and the profit made on sales so far.

    Cost uses the average of everything bought (the pooled cost HMRC uses
    for shares), so a sale takes out its share of the average cost.
    """
    pool: dict[str, dict] = {}
    realised = 0.0
    for t in trades:
        p = pool.setdefault(t['ticker'], {'quantity': 0.0, 'cost': 0.0})
        if t['side'] == 'BUY':
            p['quantity'] += t['quantity']
            p['cost'] += t['total_gbp']
        elif p['quantity'] > 0:
            sold = min(t['quantity'], p['quantity'])
            cost_out = p['cost'] * sold / p['quantity']
            realised += t['total_gbp'] - cost_out
            p['quantity'] -= sold
            p['cost'] -= cost_out
    return {k: v for k, v in pool.items() if v['quantity'] > 1e-6}, realised


def quantity_held(username: str, ticker: str) -> float:
    conn = db.get_conn()
    pool, _ = _pool(_trades(conn, username))
    conn.close()
    return pool.get(ticker, {}).get('quantity', 0.0)


def _value_history(trades: list[dict], pounds: pd.DataFrame) -> dict:
    """Daily value of the holdings against the money put in."""
    if not trades or pounds.empty:
        return {}
    df = pd.DataFrame(trades)
    df['trade_date'] = pd.to_datetime(df['trade_date'])
    df['signed_qty'] = np.where(df['side'] == 'BUY', df['quantity'], -df['quantity'])
    df['signed_cash'] = np.where(df['side'] == 'BUY', df['total_gbp'], -df['total_gbp'])

    days = pounds.index[pounds.index >= df['trade_date'].min()]
    if len(days) == 0:
        return {}
    value = pd.Series(0.0, index=days)
    for ticker, grp in df.groupby('ticker'):
        if ticker not in pounds.columns:
            continue
        qty = grp.groupby('trade_date')['signed_qty'].sum().cumsum()
        qty = qty.reindex(days, method='ffill').fillna(0.0)
        value = value + qty * pounds[ticker].ffill().reindex(days)
    invested = df.groupby('trade_date')['signed_cash'].sum().cumsum()
    invested = invested.reindex(days, method='ffill').fillna(0.0)
    return {
        'x':        [d.strftime('%Y-%m-%d') for d in days],
        'value':    [round(float(v), 2) for v in value.fillna(0.0)],
        'invested': [round(float(v), 2) for v in invested],
    }


# ---------------------------------------------------------------------------
# Signals and outlook
# ---------------------------------------------------------------------------

def _change(series: pd.Series, days: int) -> float | None:
    if len(series) <= days:
        return None
    return float(series.iloc[-1] / series.iloc[-1 - days] - 1)


def _yearly_rate(series: pd.Series, years: int) -> float | None:
    days = years * TRADING_DAYS
    if len(series) <= days:
        return None
    return float((series.iloc[-1] / series.iloc[-1 - days]) ** (1 / years) - 1)


def _max_fall(series: pd.Series) -> float:
    return float((series / series.cummax() - 1).min())


def _simulate(log_returns: np.ndarray, weights: np.ndarray, target: float,
              sims: int = 5000, block: int = 21) -> dict:
    """
    Range of one-year results if the past repeated in a random order.

    Picks 12 random one-month runs of real daily moves from the history and
    joins them into a year, many times over. Using whole months keeps bad
    days clustered together as they are in real markets, and using the same
    dates for every share keeps the way they move together.
    """
    n = len(log_returns)
    if n < block * 12:
        return {}
    rng = np.random.default_rng(7)  # fixed so figures do not jump between page loads
    months = TRADING_DAYS // block
    starts = rng.integers(0, n - block + 1, size=(sims, months))
    running = np.vstack([np.zeros((1, log_returns.shape[1])), np.cumsum(log_returns, axis=0)])
    year_log = (running[starts + block] - running[starts]).sum(axis=1)  # sims x shares
    results = np.exp(year_log) @ weights - 1
    low, lowish, mid, highish, high = np.percentile(results, [5, 25, 50, 75, 95])
    return {
        'p5': float(low), 'p25': float(lowish), 'p50': float(mid),
        'p75': float(highish), 'p95': float(high),
        'chance_target': float((results >= target).mean()),
        'chance_loss':   float((results < 0).mean()),
        'chance_loss10': float((results < -0.10).mean()),
        'years':         round(n / TRADING_DAYS, 1),
    }


def _trend_rule_test(series: pd.Series) -> dict:
    """
    How "only hold while above the 200-day average" would have done.

    Checked at each month end and applied to the following month; out of the
    market it sits in cash earning nothing. Compared with holding throughout.
    """
    average = series.rolling(200).mean()
    above = (series > average)[average.notna()]
    if len(above) < TRADING_DAYS * 2:
        return {}
    month_of = above.index.to_period('M')
    month_end = above.groupby(month_of).last().shift(1)
    # The first month has no earlier month end to go on, so it counts as not held.
    held = pd.Series(month_end.reindex(month_of).to_numpy(), index=above.index).eq(True).astype(float)

    daily = series.pct_change().reindex(above.index).fillna(0.0)
    rule = (1 + daily * held).cumprod()
    hold = (1 + daily).cumprod()
    years = len(above) / TRADING_DAYS
    return {
        'years':      round(years, 1),
        'rule_rate':  float(rule.iloc[-1] ** (1 / years) - 1),
        'hold_rate':  float(hold.iloc[-1] ** (1 / years) - 1),
        'rule_fall':  _max_fall(rule),
        'hold_fall':  _max_fall(hold),
        'time_held':  float(held.mean()),
    }


def _stats(series: pd.Series, target: float) -> dict:
    """Everything shown for one share. `series` is in pounds, dividends included."""
    s = series.dropna()
    st: dict = {
        'days':      len(s),
        'last_date': s.index[-1].strftime('%Y-%m-%d'),
        'r1d': _change(s, 1), 'r1m': _change(s, 21), 'r3m': _change(s, 63),
        'r6m': _change(s, 126), 'r1y': _change(s, TRADING_DAYS),
        'rate3': _yearly_rate(s, 3), 'rate5': _yearly_rate(s, 5),
        'enough': len(s) > TRADING_DAYS + 10,
    }
    if not st['enough']:
        return st

    log_returns = np.log(s / s.shift(1)).dropna()
    st['swing'] = float(log_returns.iloc[-TRADING_DAYS:].std() * np.sqrt(TRADING_DAYS))
    st['max_fall'] = _max_fall(s)
    st['off_high'] = float(s.iloc[-1] / s.iloc[-TRADING_DAYS:].max() - 1)

    avg50, avg200 = s.iloc[-50:].mean(), s.iloc[-200:].mean()
    st['vs_200d'] = float(s.iloc[-1] / avg200 - 1)
    st['avg50_above'] = bool(avg50 > avg200)
    # Last 12 months leaving out the latest month, which tends to reverse.
    st['momentum'] = float(s.iloc[-22] / s.iloc[-1 - TRADING_DAYS] - 1)

    year_results = s.pct_change(TRADING_DAYS).dropna()
    st['hit_target'] = float((year_results >= target).mean())
    st['lost_money'] = float((year_results < 0).mean())
    st['best_year'] = float(year_results.max())
    st['worst_year'] = float(year_results.min())

    st['outlook'] = _simulate(log_returns.to_numpy().reshape(-1, 1), np.array([1.0]), target)
    st['rule_test'] = _trend_rule_test(s)
    return st


def _score(rows: list[dict]) -> None:
    """Give each share a 0-100 trend score, a label and the reasons for it."""
    ranked = sorted((r for r in rows if r.get('enough')), key=lambda r: r['momentum'])
    for position, r in enumerate(ranked):
        r['momentum_rank'] = (position + 1) / len(ranked)

    for r in rows:
        if not r.get('enough'):
            r.update(score=None, label='Not Enough History', reasons=[
                'Needs over a year of prices before a signal can be worked out.'])
            continue
        points, why = 0, []
        if r['vs_200d'] > 0:
            points += 30
            why.append(f"Price is {r['vs_200d']:.0%} above its 200-day average (rising trend).")
        else:
            why.append(f"Price is {-r['vs_200d']:.0%} below its 200-day average (falling trend).")
        if r['avg50_above']:
            points += 10
        if r['momentum'] > 0:
            points += 20
            why.append(f"Up {r['momentum']:.0%} over the last 12 months (leaving out the latest month).")
        else:
            why.append(f"Down {-r['momentum']:.0%} over the last 12 months (leaving out the latest month).")
        points += round(20 * r['momentum_rank'])
        if r['r6m'] is not None and r['r6m'] > 0:
            points += 10
        if r['off_high'] > -0.10:
            points += 10
        else:
            why.append(f"Currently {-r['off_high']:.0%} below its high of the last year.")
        r['score'] = points
        r['label'] = 'Buy Candidate' if points >= 70 else 'Hold / Watch' if points >= 40 else 'Avoid For Now'
        r['reasons'] = why


# ---------------------------------------------------------------------------
# Page data
# ---------------------------------------------------------------------------

def _market_rows(close: pd.DataFrame, instruments: dict[str, dict]) -> list[dict]:
    rows = []
    for ticker, info in instruments.items():
        if info['role'] != 'market' or ticker not in close.columns:
            continue
        s = close[ticker].dropna()
        if s.empty:
            continue
        rows.append({
            'ticker': ticker, 'name': info['name'], 'last': float(s.iloc[-1]),
            'last_date': s.index[-1].strftime('%Y-%m-%d'),
            'r1d': _change(s, 1), 'r1m': _change(s, 21), 'r1y': _change(s, TRADING_DAYS),
        })
    return sorted(rows, key=lambda r: r['name'])


def dashboard(username: str) -> dict:
    settings = get_settings()
    conn = db.get_conn()
    instruments = _instruments(conn)
    close = _price_frame(conn, adjusted=False)
    trades = _trades(conn, username)
    news = [dict(r) for r in conn.execute(
        '''SELECT topic, source, title, link, published FROM share_news
           ORDER BY (topic = ?) DESC, COALESCE(published, fetched_at) DESC LIMIT 12''',
        (HOLDINGS_TOPIC,)
    )]
    last_refresh = _config_get(conn, 'shares_last_refresh')
    conn.close()

    pounds = _in_pounds(close, instruments) if not close.empty else pd.DataFrame()
    pool, realised = _pool(trades)

    holdings = []
    for ticker, p in pool.items():
        series = pounds[ticker].dropna() if ticker in pounds.columns else pd.Series(dtype=float)
        price = float(series.iloc[-1]) if len(series) else None
        value = p['quantity'] * price if price is not None else None
        holdings.append({
            'ticker':   ticker,
            'name':     instruments.get(ticker, {}).get('name', ticker),
            'quantity': p['quantity'],
            'cost':     p['cost'],
            'avg_cost': p['cost'] / p['quantity'],
            'price':    price,
            'value':    value,
            'gain':     value - p['cost'] if value is not None else None,
            'gain_pct': value / p['cost'] - 1 if value is not None and p['cost'] else None,
            'day_pct':  _change(series, 1) if len(series) > 1 else None,
        })
    holdings.sort(key=lambda h: -(h['value'] or 0))

    cost = sum(h['cost'] for h in holdings)
    value = sum(h['value'] or 0 for h in holdings)
    to_invest = settings['total_savings'] - settings['reserve']
    for h in holdings:
        h['share'] = (h['value'] or 0) / value if value else None

    return {
        'settings':     settings,
        'to_invest':    to_invest,
        'left_to_invest': to_invest - cost,
        'holdings':     holdings,
        'cost':         cost,
        'value':        value,
        'gain':         value - cost,
        'gain_pct':     value / cost - 1 if cost else None,
        'realised':     realised,
        'target_gain':  to_invest * settings['target_pct'] / 100,
        'markets':      _market_rows(close, instruments) if not close.empty else [],
        'history':      _value_history(trades, pounds),
        'news':         news,
        'last_refresh': last_refresh,
        'has_prices':   not close.empty,
    }


def signals(username: str) -> dict:
    settings = get_settings()
    target = settings['target_pct'] / 100
    conn = db.get_conn()
    instruments = _instruments(conn)
    adjusted = _price_frame(conn, adjusted=True)
    close = _price_frame(conn, adjusted=False)
    trades = _trades(conn, username)
    conn.close()

    rows: list[dict] = []
    pounds = _in_pounds(adjusted, instruments) if not adjusted.empty else pd.DataFrame()
    for ticker, info in instruments.items():
        if info['role'] != 'watch':
            continue
        row = {'ticker': ticker, 'name': info['name'], 'kind': info['kind'] or '',
               'currency': info['currency'] or '', 'has_prices': ticker in pounds.columns}
        if row['has_prices']:
            row.update(_stats(pounds[ticker], target))
            row['price'] = float(pounds[ticker].dropna().iloc[-1])
        rows.append(row)
    _score([r for r in rows if r['has_prices']])
    rows.sort(key=lambda r: (-(r.get('score') or -1), r['name']))

    # Outlook for what is actually held, weighted by today's value.
    portfolio = {}
    pool, _ = _pool(trades)
    close_pounds = _in_pounds(close, instruments) if not close.empty else pd.DataFrame()
    held = [t for t in pool if t in pounds.columns and t in close_pounds.columns]
    if held:
        values = np.array([pool[t]['quantity'] * float(close_pounds[t].dropna().iloc[-1]) for t in held])
        together = np.log(pounds[held] / pounds[held].shift(1)).dropna()
        portfolio = _simulate(together.to_numpy(), values / values.sum(), target)

    return {'rows': rows, 'portfolio': portfolio, 'settings': settings, 'target': target}


def plan(username: str) -> dict:
    settings = get_settings()
    conn = db.get_conn()
    platforms = [dict(r) for r in conn.execute(
        'SELECT * FROM share_platforms ORDER BY yearly_cost IS NULL, yearly_cost, name')]
    facts = [dict(r) for r in conn.execute('SELECT * FROM share_facts ORDER BY id')]
    conn.close()
    to_invest = settings['total_savings'] - settings['reserve']
    core = to_invest * settings['core_pct'] / 100
    data = signals(username)
    return {
        'settings':  settings,
        'to_invest': to_invest,
        'core':      core,
        'picks':     to_invest - core,
        'target_gain': to_invest * settings['target_pct'] / 100,
        'platforms': platforms,
        'facts':     facts,
        'history':   [r for r in data['rows'] if r.get('enough')],
    }


def news_page() -> dict:
    conn = db.get_conn()
    rows = [dict(r) for r in conn.execute(
        '''SELECT topic, source, title, link, published FROM share_news
           ORDER BY COALESCE(published, fetched_at) DESC''')]
    feeds = [dict(r) for r in conn.execute('SELECT * FROM share_feeds ORDER BY topic, name')]
    conn.close()
    order = [HOLDINGS_TOPIC, 'Markets', 'Forecasts', 'Economy', 'Politics', 'World & Conflict']
    topics: dict[str, list] = {}
    for r in rows:
        topics.setdefault(r['topic'], []).append(r)
    names = [t for t in order if t in topics] + sorted(t for t in topics if t not in order)
    return {'topics': [(t, topics[t][:60]) for t in names], 'feeds': feeds}


if __name__ == '__main__':
    if sys.argv[1:] == ['refresh']:
        db.init_db()
        init()
        refresh_all()
        for line in refresh_status()['errors']:
            print('[shares]', line)
        print('[shares] refresh finished')
    else:
        print('Usage: python shares.py refresh')
