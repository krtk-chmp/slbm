#!/usr/bin/env python3
"""Daily pipeline: download NSE files -> store -> score.

Usage:
  python run_daily.py                 # fetch today (or latest trading day)
  python run_daily.py --backfill 90   # first run: pull last 90 calendar days
"""
import argparse
import logging
import sys
from datetime import date, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from slbm import db, ingest, signals
from slbm.nse import NSEClient, ARCHIVES

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("slbm")

DB_PATH = Path(__file__).parent / "data" / "slbm.db"


def fetch_day(nse: NSEClient, con, d: date, skip_existing=True, light=False) -> bool:
    """Fetch files for one date. light=True -> SLB files only (fast deep history)."""
    ds = d.isoformat()
    if d.weekday() >= 5:
        return False
    if skip_existing and db.has_date(con, "slb_trades", ds):
        return True

    slb = nse.slbm_bhavcopy(d)
    if slb is None:
        log.info("%s: no SLBM file (holiday or not yet published)", ds)
        return False
    n = ingest.store_slbm_bhavcopy(con, d, slb)
    log.info("%s: %d SLB trade rows", ds, n)

    op = nse.slb_open_positions(d)
    if op:
        ingest.store_open_positions(con, d, op)
    if light:
        return True
    eq = nse.equity_bhavdata(d)
    if eq:
        ingest.store_equity_bhavdata(con, d, eq)
    fo = nse.fo_bhavcopy(d)
    if fo:
        ingest.store_fo_bhavcopy(con, d, fo)
    pb = nse._get(f"{ARCHIVES}/content/nsccl/fao_participant_oi_{d:%d%m%Y}.csv", tries=1)
    if pb:
        ingest.store_participant_oi(con, d, pb.decode("utf-8", "replace"))
    return True


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--backfill", type=int, default=0, help="calendar days of history to pull")
    ap.add_argument("--date", type=str, default=None, help="fetch a specific date (YYYY-MM-DD)")
    ap.add_argument("--light", action="store_true", help="with --backfill: SLB files only (fast, for years of history)")
    ap.add_argument("--fo-days", type=int, default=0, help="re-pull F&O files for last N days (fills put/call history)")
    args = ap.parse_args()

    con = db.connect(DB_PATH)
    nse = NSEClient()

    if args.backfill:
        d = date.today() - timedelta(days=args.backfill)
        while d <= date.today():
            fetch_day(nse, con, d, light=args.light)
            d += timedelta(days=1)
    elif args.fo_days:
        d = date.today() - timedelta(days=args.fo_days)
        while d <= date.today():
            if d.weekday() < 5:
                fo = nse.fo_bhavcopy(d)
                if fo:
                    n = ingest.store_fo_bhavcopy(con, d, fo)
                    log.info("%s: FO stored (%d futures rows)", d, n)
            d += timedelta(days=1)
    elif args.date:
        fetch_day(nse, con, date.fromisoformat(args.date), skip_existing=False)
    else:
        # catch up any missed recent days, then today (files appear ~7pm IST)
        d = date.today() - timedelta(days=6)
        while d < date.today():
            fetch_day(nse, con, d, skip_existing=True)
            d += timedelta(days=1)
        fetch_day(nse, con, date.today(), skip_existing=False)

    # corporate actions: always refresh the forthcoming snapshot
    ca = nse.corporate_actions()
    if ca:
        n = ingest.store_corp_actions(con, ca)
        log.info("corporate actions: %d relevant records", n)
    else:
        log.warning("corporate actions unavailable this run (will retry tomorrow)")

    signals.compute_scores(con, date.today())
    try:
        import backtest
        backtest.main()
        log.info("signal backtest refreshed")
    except Exception as e:
        log.warning("backtest skipped: %s", e)
    log.info("done. database: %s", DB_PATH)


if __name__ == "__main__":
    main()
