#!/usr/bin/env python3
"""Fast deep backfill of SLBM history (fees + open positions only).

Downloads in parallel threads, writes on the main thread (SQLite likes one writer).
Usage: python deep_backfill.py 1830        # ~5 years
"""
import sys
import logging
from concurrent.futures import ThreadPoolExecutor
from datetime import date, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from slbm import db, ingest
from slbm.nse import NSEClient

logging.basicConfig(level=logging.WARNING)
DB_PATH = Path(__file__).parent / "data" / "slbm.db"


def fetch(args):
    nse, d = args
    return d, nse.slbm_bhavcopy(d), nse.slb_open_positions(d)


def main(days: int):
    con = db.connect(DB_PATH)
    have = {r[0] for r in con.execute("SELECT DISTINCT date FROM slb_trades")}
    todo = []
    d = date.today() - timedelta(days=days)
    while d <= date.today():
        if d.weekday() < 5 and d.isoformat() not in have:
            todo.append(d)
        d += timedelta(days=1)
    print(f"{len(todo)} days to fetch")

    clients = [NSEClient() for _ in range(8)]
    jobs = [(clients[i % 8], d) for i, d in enumerate(todo)]
    done = 0
    with ThreadPoolExecutor(max_workers=8) as ex:
        for d, slb, op in ex.map(fetch, jobs):
            if slb:
                ingest.store_slbm_bhavcopy(con, d, slb)
            if op:
                ingest.store_open_positions(con, d, op)
            done += 1
            if done % 100 == 0:
                print(f"{done}/{len(todo)}")
    print("backfill complete")


if __name__ == "__main__":
    main(int(sys.argv[1]) if len(sys.argv) > 1 else 1830)
