"""Turn raw data into a per-stock demand score (0-100) with plain-language reasons.

This is a rules engine, not a crystal ball. It flags the known drivers of
SLBM lending-fee demand:
  1. Upcoming dividend / corporate action (borrowers pay up near record dates)
  2. Lending fee already trending up (7-day vs 30-day average)
  3. SLB volume trending up
  4. Open positions building
  5. Negative cost of carry in futures (= shorting pressure = borrow demand)
"""
import json
import logging
import sqlite3
from datetime import date, datetime, timedelta

log = logging.getLogger("slbm")


def _trading_dates(con, upto: str, n: int) -> list[str]:
    rows = con.execute(
        "SELECT DISTINCT date FROM slb_trades WHERE date<=? ORDER BY date DESC LIMIT ?",
        (upto, n),
    ).fetchall()
    return [r[0] for r in rows]


def compute_scores(con: sqlite3.Connection, d: date) -> int:
    ds = d.isoformat()
    last30 = _trading_dates(con, ds, 30)
    if not last30:
        return 0
    last7 = last30[:7]
    latest = last30[0]  # most recent trading day with SLB data

    # per-symbol daily aggregates: volume-weighted close fee + total qty
    q = f"""
        SELECT symbol, date,
               SUM(close*qty)/NULLIF(SUM(qty),0) AS wfee,
               SUM(qty) AS qty,
               MIN(julianday(rev_leg_date) - julianday(date)) AS min_days_to_rev
        FROM slb_trades
        WHERE date IN ({','.join('?'*len(last30))}) AND qty > 0
        GROUP BY symbol, date
    """
    hist: dict[str, dict[str, tuple]] = {}
    for sym, dt, wfee, qty, days_rev in con.execute(q, last30):
        hist.setdefault(sym, {})[dt] = (wfee or 0.0, qty or 0, days_rev or 30)

    # spot prices on latest day
    spot = dict(con.execute("SELECT symbol, close FROM prices WHERE date=?", (latest,)))

    # open positions: latest vs ~7 trading days ago
    op_now = dict(con.execute(
        "SELECT symbol, SUM(qty) FROM slb_openpos WHERE date=? GROUP BY symbol", (latest,)))
    op_then = dict(con.execute(
        "SELECT symbol, SUM(qty) FROM slb_openpos WHERE date=? GROUP BY symbol",
        (last7[-1],)))

    # cost of carry from nearest future, annualized %
    coc: dict[str, float] = {}
    for sym, expiry, fut, und in con.execute(
        """SELECT symbol, MIN(expiry), fut_close, spot_close
           FROM futures WHERE date=? AND spot_close>0 GROUP BY symbol""", (latest,)):
        try:
            days = (datetime.strptime(expiry, "%Y-%m-%d").date() - d).days or 1
            coc[sym] = (fut - und) / und * (365 / max(days, 1)) * 100
        except Exception:
            pass

    # put-call ratio: latest day and ~7 trading days ago
    pcr_now, pcr_then = {}, {}
    for target, dt in ((pcr_now, latest), (pcr_then, last7[-1])):
        for sym, ce, pe in con.execute(
            "SELECT symbol, ce_oi, pe_oi FROM fo_options WHERE date=?", (dt,)):
            if ce and ce > 0:
                target[sym] = pe / ce

    # upcoming corporate actions (next 30 days)
    upcoming: dict[str, tuple[int, str]] = {}
    horizon = (d + timedelta(days=30)).isoformat()
    for sym, ex, subj in con.execute(
        "SELECT symbol, ex_date, subject FROM corp_actions WHERE ex_date>=? AND ex_date<=?",
        (ds, horizon)):
        days = (datetime.strptime(ex, "%Y-%m-%d").date() - d).days
        if sym not in upcoming or days < upcoming[sym][0]:
            upcoming[sym] = (days, subj)

    rows = []
    for sym, days in hist.items():
        recs30 = [days[dt] for dt in last30 if dt in days]
        recs7 = [days[dt] for dt in last7 if dt in days]
        if not recs7:
            continue
        fee7 = sum(r[0] for r in recs7) / len(recs7)
        fee30 = sum(r[0] for r in recs30) / len(recs30)
        # moving averages: 5-day vs 20-day on the weighted fee
        recs5 = recs30[:5] if len(recs30) >= 5 else recs30
        recs20 = recs30[:20]
        ma5 = sum(r[0] for r in recs5) / len(recs5)
        ma20 = sum(r[0] for r in recs20) / len(recs20)
        if ma20 > 0 and ma5 > ma20 * 1.05:
            ma_trend = "up"
        elif ma20 > 0 and ma5 < ma20 * 0.95:
            ma_trend = "down"
        else:
            ma_trend = "flat"
        qty7 = sum(r[1] for r in recs7) / len(recs7)
        qty30 = sum(r[1] for r in recs30) / len(recs30)
        fee_now, qty_now, days_rev = days.get(latest, (fee7, 0, 30))

        fee_trend = (fee7 / fee30 - 1) * 100 if fee30 > 0 else 0.0
        vol_trend = (qty7 / qty30 - 1) * 100 if qty30 > 0 else 0.0

        px = spot.get(sym, 0)
        ann_yield = (fee_now / px) * (365 / max(days_rev, 1)) * 100 if px else 0.0

        opn, opt = op_now.get(sym, 0), op_then.get(sym, 0)
        op_chg = (opn / opt - 1) * 100 if opt else (100.0 if opn else 0.0)

        c = coc.get(sym)
        pcr = pcr_now.get(sym)
        div_days, div_subj = upcoming.get(sym, (None, None))

        # ---- score ----
        score, reasons = 15, []
        if div_days is not None:
            pts = round(35 * max(0, 1 - div_days / 30))
            score += pts
            reasons.append(f"{div_subj} — ex-date in {div_days} day{'s' if div_days != 1 else ''}")
        if fee_trend > 5:
            score += min(20, round(fee_trend / 5) * 4)
            reasons.append(f"Lending fee rising ({fee_trend:+.0f}% vs 30-day average)")
        elif fee_trend < -10:
            score -= 10
            reasons.append(f"Lending fee falling ({fee_trend:+.0f}% vs 30-day average)")
        if vol_trend > 25:
            score += 10
            reasons.append(f"Lending volume up {vol_trend:+.0f}%")
        if op_chg > 15:
            score += 10
            reasons.append(f"Borrowed positions building ({op_chg:+.0f}% in a week)")
        if c is not None and c < -2:
            score += min(15, round(-c))
            reasons.append(f"Futures signal shorting pressure (cost of carry {c:.1f}%)")
        if ma_trend == "up":
            score += 5
            reasons.append("Fee above its 20-day moving average (uptrend)")
        if pcr is not None and pcr > 1.2:
            delta = pcr - pcr_then.get(sym, pcr)
            if delta > 0.05:
                score += 5
                reasons.append(f"Put-call ratio {pcr:.2f} and rising — bearish options positioning")
            else:
                reasons.append(f"Put-call ratio {pcr:.2f} — more puts than calls")
        if ann_yield > 5:
            score += 5
            reasons.append(f"Already paying well ({ann_yield:.1f}% annualised)")
        score = max(0, min(100, score))
        if not reasons:
            reasons.append("No strong demand signals right now")

        rows.append((
            latest, sym, score, round(fee_now, 2), round(ann_yield, 2),
            round(fee_trend, 1), round(vol_trend, 1), round(op_chg, 1),
            round(c, 2) if c is not None else None, div_days, json.dumps(reasons),
            round(pcr, 2) if pcr is not None else None, ma_trend,
        ))

    con.executemany("INSERT OR REPLACE INTO scores VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)", rows)
    con.execute("INSERT OR REPLACE INTO meta VALUES ('latest_score_date', ?)", (latest,))
    con.commit()
    log.info("scored %d symbols for %s", len(rows), latest)
    return len(rows)
