#!/usr/bin/env python3
"""Deep backfill: 5 years of F&O aggregates + prices + participant OI.

Stores (small on purpose, to keep the database under GitHub's limits):
  - futures: front-month contract per stock per day (for cost of carry)
  - fo_options: total CE/PE open interest per stock per day (for PCR)
  - prices: close price for F&O + SLBM stocks only
  - participant_oi: FII/DII/Pro/Client market-wide positions

Handles both NSE file formats (old pre-Jul-2024, UDIFF after).
Usage: python backfill_fo.py 1830
"""
import io
import sys
import csv
import logging
import sqlite3
import zipfile
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from slbm import db
from slbm.nse import NSEClient

logging.basicConfig(level=logging.WARNING)
DB_PATH = Path(__file__).parent / "data" / "slbm.db"
ARCH = "https://nsearchives.nseindia.com"


def fo_urls(d: date) -> list[str]:
    udiff = f"{ARCH}/content/fo/BhavCopy_NSE_FO_0_0_0_{d:%Y%m%d}_F_0000.csv.zip"
    old = f"{ARCH}/content/historical/DERIVATIVES/{d:%Y}/{d:%b}/fo{d:%d%b%Y}bhav.csv.zip".upper().replace("HTTPS", "https").replace("CONTENT/HISTORICAL/DERIVATIVES", "content/historical/DERIVATIVES")
    # build old url carefully: month dir upper, filename like fo06JUL2021bhav.csv.zip
    old = f"{ARCH}/content/historical/DERIVATIVES/{d.year}/{d.strftime('%b').upper()}/fo{d.strftime('%d%b%Y').upper()}bhav.csv.zip"
    return [udiff, old] if d >= date(2024, 7, 1) else [old, udiff]


def parse_fo(text: str, d: date):
    """-> (futures front-month rows, {sym: [ce, pe]})"""
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    rdr = csv.DictReader(io.StringIO(text))
    fut: dict[str, tuple[str, float, float, int]] = {}   # sym -> (expiry, close, spot, oi)
    opt: dict[str, list[int]] = {}
    for r in rdr:
        if "FinInstrmTp" in r:  # UDIFF
            t = r.get("FinInstrmTp")
            sym = (r.get("TckrSymb") or "").strip()
            if t == "STF":
                exp = r.get("XpryDt", "")
                if sym not in fut or exp < fut[sym][0]:
                    fut[sym] = (exp, float(r.get("ClsPric") or 0),
                                float(r.get("UndrlygPric") or 0), int(float(r.get("OpnIntrst") or 0)))
            elif t == "STO":
                side = (r.get("OptnTp") or "").strip()
                if side in ("CE", "PE"):
                    a = opt.setdefault(sym, [0, 0])
                    a[0 if side == "CE" else 1] += int(float(r.get("OpnIntrst") or 0))
        else:  # old format
            inst = (r.get("INSTRUMENT") or "").strip()
            sym = (r.get("SYMBOL") or "").strip()
            if inst == "FUTSTK":
                try:
                    exp = datetime.strptime(r.get("EXPIRY_DT", "").strip(), "%d-%b-%Y").strftime("%Y-%m-%d")
                except ValueError:
                    continue
                if sym not in fut or exp < fut[sym][0]:
                    fut[sym] = (exp, float(r.get("CLOSE") or 0), 0.0,
                                int(float(r.get("OPEN_INT") or 0)))
            elif inst == "OPTSTK":
                side = (r.get("OPTION_TYP") or "").strip()
                if side in ("CE", "PE"):
                    a = opt.setdefault(sym, [0, 0])
                    a[0 if side == "CE" else 1] += int(float(r.get("OPEN_INT") or 0))
    return fut, opt


def parse_participant(text: str):
    rows = []
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    lines = [l for l in text.splitlines() if l.strip()]
    rdr = csv.reader(io.StringIO("\n".join(lines[1:])))  # skip title line
    header = next(rdr, None)
    if not header:
        return rows
    for r in rdr:
        if not r or not r[0].strip() or r[0].strip().upper() == "TOTAL":
            continue
        try:
            # cols: Client Type, Fut Idx Long, Fut Idx Short, Fut Stk Long, Fut Stk Short, ...
            rows.append((r[0].strip(), int(float(r[3])), int(float(r[4]))))
        except (ValueError, IndexError):
            continue
    return rows  # [(category, stk_fut_long, stk_fut_short)]


def fetch(args):
    nse, d = args
    fo_txt = None
    for u in fo_urls(d):
        b = nse._get(u, tries=2)
        if b and b[:2] == b"PK":
            try:
                z = zipfile.ZipFile(io.BytesIO(b))
                fo_txt = z.read(z.namelist()[0]).decode("utf-8", "replace")
                break
            except Exception:
                pass
    eq = nse.equity_bhavdata(d)
    pb = nse._get(f"{ARCH}/content/nsccl/fao_participant_oi_{d:%d%m%Y}.csv", tries=1)
    return d, fo_txt, eq, (pb.decode("utf-8", "replace") if pb else None)


def main(days: int):
    con = db.connect(DB_PATH)
    con.execute("""CREATE TABLE IF NOT EXISTS participant_oi (
        date TEXT, category TEXT, stk_fut_long INTEGER, stk_fut_short INTEGER,
        PRIMARY KEY (date, category))""")
    have = {r[0] for r in con.execute("SELECT DISTINCT date FROM fo_options")}
    slb_syms = {r[0] for r in con.execute("SELECT DISTINCT symbol FROM slb_trades")}

    todo = []
    d = date.today() - timedelta(days=days)
    while d <= date.today():
        if d.weekday() < 5 and d.isoformat() not in have:
            todo.append(d)
        d += timedelta(days=1)
    print(f"{len(todo)} days to fetch", flush=True)

    clients = [NSEClient() for _ in range(8)]
    jobs = [(clients[i % 8], dd) for i, dd in enumerate(todo)]
    done = 0
    with ThreadPoolExecutor(max_workers=8) as ex:
        for d, fo_txt, eq, part in ex.map(fetch, jobs):
            ds = d.isoformat()
            keep = set(slb_syms)
            if fo_txt:
                fut, opt = parse_fo(fo_txt, d)
                keep |= set(fut)
                con.executemany(
                    "INSERT OR REPLACE INTO futures VALUES (?,?,?,?,?,?)",
                    [(ds, s, e, c, sp or None, oi) for s, (e, c, sp, oi) in fut.items()])
                con.executemany(
                    "INSERT OR REPLACE INTO fo_options VALUES (?,?,?,?)",
                    [(ds, s, ce, pe) for s, (ce, pe) in opt.items()])
            if eq:
                rows = []
                eq = eq.replace("\r\n", "\n").replace("\r", "\n")
                for r in csv.DictReader(io.StringIO(eq)):
                    r = {k.strip(): (v.strip() if isinstance(v, str) else v) for k, v in r.items() if k}
                    if r.get("SERIES") in ("EQ", "BE") and r.get("SYMBOL") in keep:
                        try:
                            rows.append((ds, r["SYMBOL"], float(r.get("CLOSE_PRICE") or 0),
                                         float(r.get("PREV_CLOSE") or 0),
                                         float(r["DELIV_PER"]) if r.get("DELIV_PER", "-") not in ("-", "") else None,
                                         int(float(r.get("TTL_TRD_QNTY") or 0))))
                        except ValueError:
                            continue
                con.executemany("INSERT OR REPLACE INTO prices VALUES (?,?,?,?,?,?)", rows)
            if part:
                con.executemany(
                    "INSERT OR REPLACE INTO participant_oi VALUES (?,?,?,?)",
                    [(ds, c, l, s) for c, l, s in parse_participant(part)])
            con.commit()
            done += 1
            if done % 50 == 0:
                print(f"{done}/{len(todo)}", flush=True)
    print("backfill complete", flush=True)


if __name__ == "__main__":
    main(int(sys.argv[1]) if len(sys.argv) > 1 else 1830)
