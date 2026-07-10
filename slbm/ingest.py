"""Parse NSE files and store into SQLite. All idempotent (INSERT OR REPLACE)."""
import csv
import io
import logging
import sqlite3
from datetime import date, datetime

log = logging.getLogger("slbm")


def _f(x, default=0.0):
    try:
        return float(str(x).strip())
    except (ValueError, TypeError):
        return default


def _i(x, default=0):
    try:
        return int(float(str(x).strip()))
    except (ValueError, TypeError):
        return default


def store_slbm_bhavcopy(con: sqlite3.Connection, d: date, text: str) -> int:
    rows = []
    for line in text.strip().splitlines():
        c = line.split(",")
        if len(c) < 17:
            continue
        rev = c[3].strip()
        try:  # 04-AUG-2026 -> 2026-08-04
            rev = datetime.strptime(rev, "%d-%b-%Y").strftime("%Y-%m-%d")
        except ValueError:
            pass
        rows.append((
            d.isoformat(), c[1].strip(), c[2].strip(), rev,
            _f(c[5]), _f(c[6]), _f(c[7]), _f(c[8]), _f(c[9]),
            _i(c[11]), _f(c[12]), _i(c[16]),
        ))
    con.executemany(
        "INSERT OR REPLACE INTO slb_trades VALUES (?,?,?,?,?,?,?,?,?,?,?,?)", rows
    )
    con.commit()
    return len(rows)


def store_open_positions(con: sqlite3.Connection, d: date, text: str) -> int:
    rows = []
    for r in csv.DictReader(io.StringIO(text)):
        r = {k.strip(): v for k, v in r.items() if k}
        sym = (r.get("Security") or "").strip()
        if not sym:
            continue
        rows.append((
            d.isoformat(), sym, (r.get("Series") or "").strip(),
            _i(r.get("Outstanding Quantity at the end of the day")),
        ))
    con.executemany("INSERT OR REPLACE INTO slb_openpos VALUES (?,?,?,?)", rows)
    con.commit()
    return len(rows)


def store_equity_bhavdata(con: sqlite3.Connection, d: date, text: str) -> int:
    rows = []
    for r in csv.DictReader(io.StringIO(text)):
        r = {k.strip(): (v.strip() if isinstance(v, str) else v) for k, v in r.items() if k}
        if r.get("SERIES") not in ("EQ", "BE"):
            continue
        rows.append((
            d.isoformat(), r.get("SYMBOL", ""),
            _f(r.get("CLOSE_PRICE")), _f(r.get("PREV_CLOSE")),
            _f(r.get("DELIV_PER"), default=None), _i(r.get("TTL_TRD_QNTY")),
        ))
    con.executemany("INSERT OR REPLACE INTO prices VALUES (?,?,?,?,?,?)", rows)
    con.commit()
    return len(rows)


def store_fo_bhavcopy(con: sqlite3.Connection, d: date, text: str) -> int:
    rows = []
    opt: dict[str, list[int]] = {}  # symbol -> [ce_oi, pe_oi]
    for r in csv.DictReader(io.StringIO(text)):
        typ = r.get("FinInstrmTp")
        if typ == "STF":  # single stock futures
            rows.append((
                d.isoformat(), r.get("TckrSymb", "").strip(), r.get("XpryDt", ""),
                _f(r.get("ClsPric")), _f(r.get("UndrlygPric")), _i(r.get("OpnIntrst")),
            ))
        elif typ == "STO":  # stock options -> aggregate put/call OI per symbol
            sym = r.get("TckrSymb", "").strip()
            side = r.get("OptnTp", "").strip()
            oi = _i(r.get("OpnIntrst"))
            if sym and side in ("CE", "PE"):
                a = opt.setdefault(sym, [0, 0])
                a[0 if side == "CE" else 1] += oi
    con.executemany("INSERT OR REPLACE INTO futures VALUES (?,?,?,?,?,?)", rows)
    con.executemany(
        "INSERT OR REPLACE INTO fo_options VALUES (?,?,?,?)",
        [(d.isoformat(), s, ce, pe) for s, (ce, pe) in opt.items()],
    )
    con.commit()
    return len(rows)


def store_corp_actions(con: sqlite3.Connection, records: list[dict]) -> int:
    rows = []
    for r in records:
        ex = (r.get("exDate") or "").strip()
        try:
            ex = datetime.strptime(ex, "%d-%b-%Y").strftime("%Y-%m-%d")
        except ValueError:
            continue
        subject = (r.get("subject") or "").strip()
        # keep only actions that matter for SLB rent
        if not any(w in subject.lower() for w in ("dividend", "bonus", "split", "rights")):
            continue
        rows.append(((r.get("symbol") or "").strip(), ex, subject))
    con.executemany("INSERT OR REPLACE INTO corp_actions VALUES (?,?,?)", rows)
    con.commit()
    return len(rows)
