#!/usr/bin/env python3
"""Backtest the options-positioning lean over all history in the database.

For every stock and day: compute the lean (same logic the dashboard shows),
then check what the price actually did over the next 10 trading days.
Results go to the signal_stats table, which the dashboard displays as
"similar setups over 5 years: price fell X% of the time (N cases)".
"""
import sys
import sqlite3
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).parent))
from slbm.signals import lean_points, lean_label

DB_PATH = Path(__file__).parent / "data" / "slbm.db"
HORIZON = 10  # trading days forward


def run(con: sqlite3.Connection) -> pd.DataFrame:
    opt = pd.read_sql_query(
        "SELECT date, symbol, CAST(pe_oi AS REAL)/NULLIF(ce_oi,0) AS pcr FROM fo_options", con)
    fut = pd.read_sql_query(
        "SELECT date, symbol, MIN(expiry) AS expiry, fut_close, spot_close FROM futures GROUP BY date, symbol", con)
    px = pd.read_sql_query("SELECT date, symbol, close FROM prices WHERE close > 0", con)

    df = opt.merge(fut, on=["date", "symbol"], how="left").merge(px, on=["date", "symbol"], how="left")
    df["spot"] = df["spot_close"].where(df["spot_close"] > 0, df["close"])
    days_to_exp = (pd.to_datetime(df["expiry"]) - pd.to_datetime(df["date"])).dt.days.clip(lower=1)
    df["coc"] = (df["fut_close"] - df["spot"]) / df["spot"] * (365 / days_to_exp) * 100
    df.loc[df["spot"].isna() | (df["fut_close"] <= 0), "coc"] = None

    df = df.sort_values(["symbol", "date"])
    g = df.groupby("symbol")
    df["pcr_prev"] = g["pcr"].shift(5)
    df["fwd_close"] = g["close"].shift(-HORIZON)
    df["fwd_ret"] = (df["fwd_close"] / df["close"] - 1) * 100

    df = df.dropna(subset=["pcr", "fwd_ret"])
    pts = df.apply(lambda r: lean_points(
        r["pcr"], r["pcr_prev"] if pd.notna(r["pcr_prev"]) else None,
        r["coc"] if pd.notna(r["coc"]) else None)[0], axis=1)
    lab = pts.map(lambda p: lean_label(p))
    df["direction"] = lab.map(lambda t: t[0])
    df["strength"] = lab.map(lambda t: t[1])

    out = []
    for (d, s), grp in df.groupby(["direction", "strength"]):
        if d == "mixed":
            hits = (grp["fwd_ret"].abs() < 4).sum()  # "no big move" as the neutral read
        elif d == "bearish":
            hits = (grp["fwd_ret"] < 0).sum()
        else:
            hits = (grp["fwd_ret"] > 0).sum()
        out.append((d, s, len(grp), int(hits), round(grp["fwd_ret"].mean(), 2)))
    return pd.DataFrame(out, columns=["direction", "strength", "n", "hits", "avg_ret"])


def main():
    con = sqlite3.connect(DB_PATH)
    con.execute("""CREATE TABLE IF NOT EXISTS signal_stats (
        direction TEXT, strength TEXT, n INTEGER, hits INTEGER, avg_ret REAL,
        PRIMARY KEY (direction, strength))""")
    stats = run(con)
    px = pd.read_sql_query("SELECT date, symbol, close FROM prices WHERE close>0", con)
    px = px.sort_values(["symbol", "date"])
    px["ret"] = (px.groupby("symbol")["close"].shift(-HORIZON) / px["close"] - 1) * 100
    px = px.dropna(subset=["ret"])
    base = pd.DataFrame([("base", "", len(px), int((px["ret"] > 0).sum()),
                          round(px["ret"].mean(), 2))], columns=stats.columns)
    stats = pd.concat([stats, base])
    con.execute("DELETE FROM signal_stats")
    con.executemany("INSERT INTO signal_stats VALUES (?,?,?,?,?)",
                    stats.itertuples(index=False, name=None))
    con.commit()
    print(stats.to_string(index=False))


if __name__ == "__main__":
    main()
