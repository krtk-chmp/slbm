"""Share Rent Tracker — SLBM dashboard.

Design: lowkey, Zerodha-Kite-inspired. Clean white, compact rows, numbers first.
Surface shows only what matters; tap a stock to open full detail.

Portfolio persistence (hosted filesystem resets on every data push):
  1. watchlist.txt in the repo  — "SYMBOL,QTY" per line, the default portfolio
  2. URL params ?w=SYM:QTY,...&p=SYM,...  — browser bookmark remembers edits/pins
"""
import json
import sqlite3
from pathlib import Path

import pandas as pd
import streamlit as st

ROOT = Path(__file__).parent.parent
DB = ROOT / "data" / "slbm.db"
WATCHLIST_FILE = ROOT / "watchlist.txt"

st.set_page_config(page_title="Rent Tracker", page_icon="📈", layout="centered")

st.markdown("""
<style>
  .block-container { padding-top: 2.2rem; max-width: 760px; }
  html, body, [class*="st-"] { font-family: -apple-system, "Segoe UI", Roboto, "Helvetica Neue", sans-serif; }
  #MainMenu, footer { visibility: hidden; }

  .topbar { display:flex; justify-content:space-between; align-items:baseline;
            border-bottom:1px solid #eee; padding-bottom:10px; margin-bottom:6px; }
  .brand { font-size:1.35rem; font-weight:600; color:#26323a; }
  .asof  { font-size:.8rem; color:#9aa4ab; }

  .sechead { font-size:.75rem; font-weight:600; letter-spacing:.08em; color:#9aa4ab;
             text-transform:uppercase; margin:26px 0 4px 2px; }

  .row { display:flex; justify-content:space-between; align-items:center;
         padding:12px 4px 10px 2px; border-bottom:1px solid #f2f4f5; }
  .sym  { font-size:1.02rem; font-weight:600; color:#26323a; }
  .sub  { font-size:.75rem; color:#9aa4ab; margin-top:1px; }
  .nums { text-align:right; }
  .fee  { font-size:1.02rem; font-weight:500; color:#26323a; }
  .pct-hot  { font-size:.8rem; font-weight:600; color:#1e8a4c; }
  .pct-warm { font-size:.8rem; font-weight:600; color:#c98a00; }
  .pct-quiet{ font-size:.8rem; font-weight:600; color:#9aa4ab; }

  .chip { font-size:.7rem; font-weight:600; padding:2px 9px; border-radius:10px; }
  .chip-hot   { background:#e6f4ec; color:#1e8a4c; }
  .chip-warm  { background:#fdf3dd; color:#b07c00; }
  .chip-quiet { background:#f2f4f5; color:#8a949b; }

  .why { color:#4a555e; font-size:.9rem; line-height:1.65; }
  .kv  { display:flex; gap:26px; flex-wrap:wrap; margin:4px 0 10px 0; }
  .kv div { font-size:.95rem; color:#26323a; }
  .kv span { display:block; font-size:.7rem; color:#9aa4ab; text-transform:uppercase; letter-spacing:.05em; }
  .earn { background:#f6f9fc; border-radius:8px; padding:10px 14px; font-size:.95rem;
          color:#26323a; margin:8px 0; }

  div[data-testid="stExpander"] { border:none !important; }
  div[data-testid="stExpander"] details { border:none; background:#fbfcfd; border-radius:8px; }
  .stButton button { font-size:.8rem; padding:2px 10px; border:1px solid #e6e9eb;
                     background:#fff; color:#6a747c; border-radius:6px; }
</style>
""", unsafe_allow_html=True)


# ---------- data ----------

@st.cache_resource
def get_con():
    return sqlite3.connect(DB, check_same_thread=False)


def parse_portfolio_text() -> dict[str, int]:
    pf = {}
    if WATCHLIST_FILE.exists():
        for ln in WATCHLIST_FILE.read_text().splitlines():
            ln = ln.strip()
            if not ln or ln.startswith("#"):
                continue
            parts = [p.strip() for p in ln.replace(":", ",").split(",")]
            qty = int(parts[1]) if len(parts) > 1 and parts[1].isdigit() else 0
            pf[parts[0].upper()] = qty
    return pf


def get_portfolio() -> dict[str, int]:
    raw = st.query_params.get("w")
    if raw is None:
        return parse_portfolio_text()
    pf = {}
    for item in raw.split(","):
        if not item.strip():
            continue
        parts = item.split(":")
        qty = int(parts[1]) if len(parts) > 1 and parts[1].isdigit() else 0
        pf[parts[0].strip().upper()] = qty
    return pf


def set_portfolio(pf: dict[str, int]):
    st.query_params["w"] = ",".join(
        f"{s}:{q}" if q else s for s, q in sorted(pf.items()))


def get_pins() -> list[str]:
    raw = st.query_params.get("p", "")
    return [s for s in (x.strip().upper() for x in raw.split(",")) if s]


def set_pins(pins: list[str]):
    if pins:
        st.query_params["p"] = ",".join(sorted(set(pins)))
    elif "p" in st.query_params:
        del st.query_params["p"]


def band(score: int) -> tuple[str, str]:
    if score >= 60:
        return "hot", "In demand"
    if score >= 35:
        return "warm", "Warming up"
    return "quiet", "Quiet"


@st.cache_data(ttl=3600)
def fee_history(sym: str) -> pd.DataFrame:
    h = pd.read_sql_query(
        """SELECT date, SUM(close*qty)/NULLIF(SUM(qty),0) AS fee, SUM(qty) AS volume
           FROM slb_trades WHERE symbol=? GROUP BY date ORDER BY date""",
        get_con(), params=(sym,))
    h["date"] = pd.to_datetime(h["date"])
    return h


# ---------- one stock ----------

def stock_row(row, qty: int, pinned: bool, pins: list[str], key: str):
    cls, label = band(row["score"])
    c1, c2 = st.columns([11, 1])
    sub = f"{qty:,} shares" if qty else ("pinned" if pinned else "SLBM")
    with c1:
        st.markdown(
            f"""<div class="row">
              <div>
                <div class="sym">{'📌 ' if pinned else ''}{row['symbol']}</div>
                <div class="sub">{sub}</div>
              </div>
              <div class="nums">
                <div class="fee">₹{row['fee_close']:.2f}</div>
                <div class="pct-{cls}">{row['ann_yield']:.1f}% / yr</div>
              </div>
              <div><span class="chip chip-{cls}">{label} · {row['score']}</span></div>
            </div>""",
            unsafe_allow_html=True)
    with c2:
        if st.button("📌" if not pinned else "✖", key=f"pin_{key}",
                     help="Pin to top" if not pinned else "Unpin"):
            set_pins([p for p in pins if p != row["symbol"]] if pinned
                     else pins + [row["symbol"]])
            st.rerun()

    with st.expander("Details"):
        detail(row, qty)


def detail(row, qty: int):
    trend_word = {"up": "Uptrend ▲", "down": "Downtrend ▼", "flat": "Flat →"}.get(row.get("ma_trend") or "flat")
    pcr = f"{row['pcr']:.2f}" if pd.notna(row.get("pcr")) else "—"
    st.markdown(
        f"""<div class="kv">
          <div><span>Fee today</span>₹{row['fee_close']:.2f}</div>
          <div><span>Return rate</span>{row['ann_yield']:.1f}% / yr</div>
          <div><span>Trend (5 vs 20-day avg)</span>{trend_word}</div>
          <div><span>Put-call ratio</span>{pcr}</div>
          <div><span>Score</span>{row['score']} / 100</div>
        </div>""",
        unsafe_allow_html=True)

    if qty:
        est = row["fee_close"] * qty
        st.markdown(
            f"""<div class="earn">Your {qty:,} shares ≈ <b>₹{est:,.0f}</b> if lent at
            today's fee (for the current lending period).</div>""",
            unsafe_allow_html=True)

    st.markdown('<div class="why">' +
                "<br>".join(f"•&nbsp; {r}" for r in json.loads(row["reasons"])) +
                "</div>", unsafe_allow_html=True)

    h = fee_history(row["symbol"])
    if len(h) > 5:
        st.markdown("**Recent fee, with 5 & 20-day averages**")
        recent = h.tail(130).set_index("date").copy()
        recent["5-day avg"] = recent["fee"].rolling(5).mean()
        recent["20-day avg"] = recent["fee"].rolling(20).mean()
        st.line_chart(recent[["fee", "5-day avg", "20-day avg"]], height=230)

        if h["date"].dt.year.nunique() >= 2:
            st.markdown("**Which months this share usually pays (5-year history)**")
            season = h.groupby(h["date"].dt.month)["fee"].mean().reindex(range(1, 13))
            season.index = ["Jan", "Feb", "Mar", "Apr", "May", "Jun",
                            "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]
            st.bar_chart(season, height=200)
            st.caption("Average lending fee by calendar month. Tall bars = months this share is usually in demand — plan around them.")

            st.markdown("**Full history (monthly average)**")
            monthly = h.set_index("date")["fee"].resample("ME").mean()
            st.line_chart(monthly, height=200)
    else:
        st.info("Chart appears once a few days of trading history exist for this share.")


# ---------- page ----------

con = get_con()
r = con.execute("SELECT value FROM meta WHERE key='latest_score_date'").fetchone()
latest = r[0] if r else None

if not latest:
    st.warning("No data yet. Run `python run_daily.py --backfill 90` once.")
    st.stop()

st.markdown(
    f"""<div class="topbar"><span class="brand">Rent Tracker</span>
    <span class="asof">data up to {latest}</span></div>""",
    unsafe_allow_html=True)

scores = pd.read_sql_query("SELECT * FROM scores WHERE date=?", con, params=(latest,))
smap = scores.set_index("symbol", drop=False)
portfolio = get_portfolio()
pins = get_pins()

# ---- sidebar: manage portfolio ----
with st.sidebar:
    st.subheader("My portfolio")
    all_syms = sorted(scores["symbol"].tolist())
    add = st.selectbox("Add share", [""] + [s for s in all_syms if s not in portfolio])
    q = st.number_input("Quantity you hold (optional)", min_value=0, step=50, value=0)
    if st.button("Add to portfolio") and add:
        portfolio[add] = int(q)
        set_portfolio(portfolio)
        st.rerun()
    rm = st.selectbox("Remove share", [""] + sorted(portfolio))
    if st.button("Remove") and rm:
        portfolio.pop(rm, None)
        set_portfolio(portfolio)
        set_pins([p for p in pins if p != rm])
        st.rerun()
    if st.query_params.get("w") is not None:
        st.success("⭐ Bookmark this page — the link remembers your portfolio and pins.")

# ---- pinned + portfolio ----
own = [s for s in portfolio if s in smap.index]
pinned_syms = [s for s in pins if s in smap.index]
ordered = pinned_syms + sorted(
    [s for s in own if s not in pinned_syms],
    key=lambda s: -int(smap.loc[s, "score"]))

st.markdown('<div class="sechead">My shares</div>', unsafe_allow_html=True)
if not ordered and not portfolio:
    st.info("Add your shares from the left panel (tap » on mobile). They stay in your bookmark.")
for s in ordered:
    stock_row(smap.loc[s], portfolio.get(s, 0), s in pins, pins, key=f"my_{s}")
not_trading = [s for s in portfolio if s not in smap.index]
if not_trading:
    st.caption(f"Not traded in SLBM recently: {', '.join(sorted(not_trading))} — no lending demand there right now.")

# ---- market: only good-rated on the surface ----
st.markdown('<div class="sechead">In demand today — whole market</div>', unsafe_allow_html=True)
market = scores[~scores["symbol"].isin(set(ordered))].sort_values("score", ascending=False)
good = market[market["score"] >= 50]
show_all = st.session_state.get("show_all", False)
view = market.head(30) if show_all else good.head(12)
if view.empty:
    st.caption("Nothing strongly in demand today.")
for _, rw in view.iterrows():
    stock_row(rw, portfolio.get(rw["symbol"], 0), rw["symbol"] in pins, pins, key=f"mkt_{rw['symbol']}")
if not show_all and len(market) > len(view):
    if st.button("Show more shares"):
        st.session_state["show_all"] = True
        st.rerun()

with st.expander("How to read this page"):
    st.markdown("""
- **Fee** — the rent someone pays you per share to borrow it for the current period (usually till month-end).
- **% / yr** — that fee converted to a yearly return rate on the share's price, so you can compare shares fairly.
- **In demand / Warming up / Quiet** — our 0–100 score from five signals: upcoming dividends, fee trend vs its averages, lending volume, borrowed positions building, and futures/options positioning (cost of carry, put-call ratio).
- **Months chart** — 5 years of history showing which calendar months this share usually pays. This answers "why was it hot last month and dead now."
- Signals, **not guarantees** — the fee is set by market auction every day. Lending happens through your broker's SLBM facility.
""")
