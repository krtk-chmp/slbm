# Share Rent Tracker (NSE SLBM)

Tracks lending-fee ("rent") demand for NSE SLBM stocks and shows a simple
dashboard: which shares are in demand for lending, which are heating up, and why.

- **Backend**: `run_daily.py` — downloads NSE end-of-day files, stores them in
  `data/slbm.db` (SQLite), computes a 0–100 demand score per stock.
- **Frontend**: `dashboard/app.py` — Streamlit page your parents open. Watchlist,
  plain-language reasons, fee history charts. No jargon on screen.
- The database in this folder already contains **5 years of real SLBM history**
  (July 2021 onwards, ~100k lending records), so charts, moving averages, and
  month-by-month seasonality work from day one.

### Portfolio file
`watchlist.txt` is the default portfolio — one line per share, optional quantity:
```
RELIANCE,100
TITAN,250
HINDALCO
```
Edit it directly on GitHub anytime. Your parents can also add/remove/pin shares
in the browser; those edits live in the page URL, so they just re-bookmark.

## Option A — fully hosted, free, nothing runs on your machine (recommended)

1. Create a GitHub repo and push this whole folder to it (keep `data/slbm.db`).
2. In the repo: **Settings → Actions → General → Workflow permissions →
   "Read and write permissions"** → Save.
3. Done with the backend. `.github/workflows/daily.yml` now runs every weekday
   at 9 PM IST, pulls the day's NSE data, and commits the updated database.
   (Test it once: Actions tab → "Daily SLBM data pull" → Run workflow.)
4. Frontend: go to https://share.streamlit.io → New app → pick your repo →
   main file `dashboard/app.py` → Deploy.
5. Send the app URL to your parents. Streamlit Cloud redeploys automatically
   whenever the Action commits new data, so the page updates itself every night.

## Option B — run on your own machine/server

```bash
pip install -r requirements.txt
python deep_backfill.py 1830             # once: 5 years of SLBM history (~3 min)
python run_daily.py --backfill 40        # once: recent prices/futures/options
streamlit run dashboard/app.py           # the dashboard
```

Cron entry (9 PM IST daily):
```
0 21 * * 1-5 cd /path/to/slbm-platform && python3 run_daily.py >> cron.log 2>&1
```

## What the score means

Rules, not magic. Points come from the known drivers of SLBM rent:

| Signal | Why it matters |
|---|---|
| Dividend/bonus ex-date within 30 days | Borrowers pay high rent around record dates (the biggest driver) |
| Lending fee 7-day avg vs 30-day avg | Rent already trending up |
| SLB volume trend | Borrowing activity picking up |
| Open positions building | Sustained borrow demand |
| Negative cost of carry (futures below spot) | Shorting pressure → borrow demand |
| Fee above its 20-day moving average | Momentum/uptrend confirmation |
| Put-call ratio high & rising | Bearish options positioning → borrow demand |

60+ = IN DEMAND (green), 35–59 = WARMING UP (yellow), below = QUIET.

## Options & positioning data (added later)

- 5 years of F&O history: put-call OI, front-month futures, prices for all F&O
  stocks, FII/DII/Pro/Client participant positions. `backfill_fo.py` rebuilds it.
- Strike-level option positions (nearest expiry, ±15% of price, rolling 3 weeks)
  power the "where the bets sit" chart.
- `backtest.py` re-tests the positioning signal against 5 years of history on
  every nightly run and stores hit rates in `signal_stats`.

**Backtest verdict (why the app doesn't say "buy puts/calls"):** across ~68,000
put-heavy or call-heavy setups in 5 years, the signal predicted direction no
better than chance (bearish setups: price fell only 44% of the time; bullish:
53% vs a 53% market base rate). The dashboard therefore shows positioning as
information with its measured track record attached, never as a trade call.

## Things that will eventually need attention

- **NSE changes file URLs every year or two.** If the Action starts logging
  "no SLBM file" on normal trading days, the URL patterns in `slbm/nse.py`
  need updating. Everything is in that one file.
- Corporate-actions data comes from NSE's main API, which sometimes blocks
  cloud IPs. The run continues without it and retries next day; only the
  dividend signal pauses.
- Data is end-of-day. Files appear ~7–9 PM IST; the dashboard shows the
  previous close each morning — which is the right data for a morning check.
- The database is ~50 MB and grows slowly (~15 KB/day of SLBM data plus
  aggregates). GitHub's file limit is 100 MB; at this rate that's years away.
  If it ever gets close, prune old `prices`/`futures` rows — 5-year charts only
  need the `slb_trades` table.
- Put-call history starts from June 2026 (options data accumulates daily
  from the nightly run; NSE F&O files are too heavy to backfill 5 years for).

## Honest limits (worth telling your parents once)

This flags *conditions* under which rent tends to rise. It cannot promise a
rate — SLBM fees are set by auction each day, and a stock with zero borrower
interest earns nothing no matter what any tool says. Lending itself happens
through their broker's SLBM facility, not through this dashboard.
