"""Share Rent Tracker — SLBM dashboard.

Design: lowkey, Zerodha-Kite-inspired. Clean white, compact rows, numbers first.
Surface shows only what matters; tap a stock to open full detail.

Portfolio persistence (hosted filesystem resets on every data push):
  1. watchlist.txt in the repo  — "SYMBOL,QTY" per line, the default portfolio
  2. URL params ?w=SYM:QTY,...&p=SYM,...  — browser bookmark remembers edits/pins
"""
import json
import sqlite3
import sys
from pathlib import Path

import pandas as pd
import streamlit as st

sys.path.insert(0, str(Path(__file__).parent.parent))
from slbm.signals import lean_points

ROOT = Path(__file__).parent.parent
DB = ROOT / "data" / "slbm.db"
WATCHLIST_FILE = ROOT / "watchlist.txt"

st.set_page_config(page_title="Rent Tracker", page_icon="📈", layout="centered")

st.markdown("""
<style>
  .block-container { padding-top: 2.2rem; max-width: 760px; }
  #MainMenu, footer { visibility: hidden; }

  .topbar { display:flex; justify-content:space-between; align-items:baseline;
            border-bottom:1px solid color-mix(in srgb, var(--text-color, #26323a) 14%, transparent);
            padding-bottom:10px; margin-bottom:6px; }
  .brand { font-size:1.35rem; font-weight:600; color: var(--text-color, #26323a); }
  .asof  { font-size:.8rem; color: color-mix(in srgb, var(--text-color, #26323a) 55%, transparent); }

  .sechead { font-size:.75rem; font-weight:600; letter-spacing:.08em;
             color: color-mix(in srgb, var(--text-color, #26323a) 55%, transparent);
             text-transform:uppercase; margin:26px 0 4px 2px; }

  .row { display:flex; justify-content:space-between; align-items:center;
         padding:12px 4px 10px 2px;
         border-bottom:1px solid color-mix(in srgb, var(--text-color, #26323a) 10%, transparent); }
  .sym  { font-size:1.02rem; font-weight:600; color: var(--text-color, #26323a); }
  .sub  { font-size:.75rem; color: color-mix(in srgb, var(--text-color, #26323a) 50%, transparent); margin-top:1px; }
  .nums { text-align:right; }
  .fee  { font-size:1.02rem; font-weight:500; color: var(--text-color, #26323a); }
  .pct-hot  { font-size:.8rem; font-weight:600; color:#21a05f; }
  .pct-warm { font-size:.8rem; font-weight:600; color:#d19412; }
  .pct-quiet{ font-size:.8rem; font-weight:600; color: color-mix(in srgb, var(--text-color, #26323a) 45%, transparent); }

  .chip { font-size:.7rem; font-weight:600; padding:2px 9px; border-radius:10px; white-space:nowrap; }
  .chip-hot   { background:rgba(46,160,95,.16); color:#21a05f; }
  .chip-warm  { background:rgba(209,148,18,.16); color:#d19412; }
  .chip-quiet { background:color-mix(in srgb, var(--text-color, #26323a) 8%, transparent);
                color: color-mix(in srgb, var(--text-color, #26323a) 55%, transparent); }

  .why { color: color-mix(in srgb, var(--text-color, #26323a) 80%, transparent);
         font-size:.9rem; line-height:1.65; }
  .kv  { display:flex; gap:26px; flex-wrap:wrap; margin:4px 0 10px 0; }
  .kv div { font-size:.95rem; color: var(--text-color, #26323a); }
  .kv span { display:block; font-size:.7rem;
             color: color-mix(in srgb, var(--text-color, #26323a) 55%, transparent);
             text-transform:uppercase; letter-spacing:.05em; }
  .earn { background: var(--secondary-background-color, #f6f9fc); border-radius:8px;
          padding:10px 14px; font-size:.95rem; color: var(--text-color, #26323a); margin:8px 0; }
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
def pcr_history(sym: str) -> pd.DataFrame:
    return pd.read_sql_query(
        """SELECT date, CAST(pe_oi AS REAL)/NULLIF(ce_oi,0) AS pcr
           FROM fo_options WHERE symbol=? ORDER BY date""",
        get_con(), params=(sym,))


def options_lean(sym: str, coc) -> tuple[str, list[str]]:
    """Descriptive positioning label + facts (no direction-picking)."""
    ph = pcr_history(sym)
    pcr = ph["pcr"].iloc[-1] if len(ph) else None
    prev = ph["pcr"].iloc[-6] if len(ph) > 6 else None
    pcr = None if pcr is None or pd.isna(pcr) else float(pcr)
    prev = None if prev is None or pd.isna(prev) else float(prev)
    coc = None if coc is None or pd.isna(coc) else float(coc)
    pts, notes = lean_points(pcr, prev, coc)
    if not notes:
        return "", []
    if pts >= 2:
        return "Put-heavy positioning", notes
    if pts <= -2:
        return "Call-heavy positioning", notes
    return "Balanced positioning", notes


@st.cache_data(ttl=3600)
def signal_stats() -> dict:
    try:
        df = pd.read_sql_query("SELECT * FROM signal_stats", get_con())
        return {(r["direction"], r["strength"]): (int(r["n"]), int(r["hits"]))
                for _, r in df.iterrows()}
    except Exception:
        return {}


def track_record(label: str) -> str | None:
    st_ = signal_stats()
    base = st_.get(("base", ""))
    if not st_ or base is None:
        return None
    base_up = base[1] / base[0] * 100
    def pooled(direction):
        rows = [v for (d, _), v in st_.items() if d == direction]
        n = sum(r[0] for r in rows); h = sum(r[1] for r in rows)
        return n, (h / n * 100 if n else 0)
    if label.startswith("Put-heavy"):
        n, fell = pooled("bearish")
        return (f"5-year test ({n:,} similar setups): price actually FELL only {fell:.0f}% "
                f"of the time — it rose more often than not. No predictive edge found; "
                f"read this as where traders are positioned, not where price will go.")
    if label.startswith("Call-heavy"):
        n, rose = pooled("bullish")
        return (f"5-year test ({n:,} similar setups): price rose {rose:.0f}% of the time — "
                f"but any stock on any day rose {base_up:.0f}% of the time. No added edge; "
                f"read this as positioning, not a prediction.")
    return None


@st.cache_data(ttl=3600)
def strike_walls(sym: str) -> pd.DataFrame:
    try:
        d = pd.read_sql_query(
            """SELECT strike, side, oi FROM fo_strikes
               WHERE symbol=? AND date=(SELECT MAX(date) FROM fo_strikes WHERE symbol=?)""",
            get_con(), params=(sym, sym))
        if d.empty:
            return d
        return d.pivot_table(index="strike", columns="side", values="oi", aggfunc="sum").rename(
            columns={"CE": "Calls OI", "PE": "Puts OI"}).fillna(0)
    except Exception:
        return pd.DataFrame()


@st.cache_data(ttl=3600)
def fee_history(sym: str) -> pd.DataFrame:
    h = pd.read_sql_query(
        """SELECT date, SUM(close*qty)/NULLIF(SUM(qty),0) AS fee, SUM(qty) AS volume
           FROM slb_trades WHERE symbol=? GROUP BY date ORDER BY date""",
        get_con(), params=(sym,))
    h["date"] = pd.to_datetime(h["date"])
    return h


# ---------- one stock ----------

def stock_row(row, qty: int, pinned: bool, pins: list[str], key: str, owned: bool = False):
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
        detail(row, qty, owned)


def action_box(row, qty: int):
    """Plain instruction for the owner: lend now / wait / nothing to do."""
    score = int(row["score"])
    sym = row["symbol"]
    fee = row["fee_close"]
    h = fee_history(sym).tail(22)
    lo, hi = (h["fee"].min(), h["fee"].max()) if len(h) else (fee, fee)
    if score >= 60:
        qty_txt = f"{qty:,}" if qty else "the shares you hold"
        earn = f" &mdash; roughly <b>&#8377;{fee*qty:,.0f}</b> for the period" if qty else ""
        st.markdown(
            f"""<div class="earn"><b>&#9989; What to do: good time to lend.</b><br>
            1. Open your broker's <b>SLB / share-lending</b> section (or call your dealer) and place a <b>LEND</b> order.<br>
            2. Stock: <b>{sym}</b> &nbsp;&middot;&nbsp; Quantity: <b>{qty_txt}</b> &nbsp;&middot;&nbsp;
               Minimum fee: around <b>&#8377;{fee:.2f}</b> per share{earn}.
               This month's fees ranged &#8377;{lo:.2f}&ndash;&#8377;{hi:.2f}; don't accept far below that.<br>
            3. If it matches, the fee money arrives by the next day. Shares return
               automatically when the period ends. If a dividend falls in between,
               the borrower pays you the equivalent &mdash; you lose nothing.</div>""",
            unsafe_allow_html=True)
    elif score >= 35:
        st.markdown(
            f"""<div class="earn"><b>&#128993; What to do: wait and watch.</b><br>
            Demand for {sym} is picking up but not strong yet. Check again in 2&ndash;3 days &mdash;
            if it turns green (IN DEMAND), lend then. Nothing to do today.</div>""",
            unsafe_allow_html=True)
    else:
        st.markdown(
            f"""<div class="earn"><b>&#9898; What to do: nothing right now.</b><br>
            Very little borrowing demand for {sym} &mdash; lending today would earn almost
            nothing. This page will show green when that changes.</div>""",
            unsafe_allow_html=True)


def quiet_row(sym: str, qty: int):
    """A share the user owns that isn't being lent right now — still show history."""
    st.markdown(
        f'<div class="row"><div><div class="sym">{sym}</div>'
        f'<div class="sub">{qty:,} shares &middot; quiet now</div></div>'
        f'<div class="nums"><div class="fee">&mdash;</div>'
        f'<div class="pct-quiet">no demand</div></div>'
        f'<div><span class="chip chip-quiet">Quiet &middot; 0</span></div></div>',
        unsafe_allow_html=True)
    with st.expander("Details"):
        st.markdown(f"**{sym} is not being lent right now** — no borrowing demand this month.")
        hist = fee_history(sym)
        if len(hist) > 5:
            st.markdown("**Which months it usually pays**")
            hh = hist.copy()
            hh["date"] = pd.to_datetime(hh["date"])
            season = hh.groupby(hh["date"].dt.month)["fee"].mean().reindex(range(1, 13))
            season.index = ["01 Jan", "02 Feb", "03 Mar", "04 Apr", "05 May", "06 Jun",
                            "07 Jul", "08 Aug", "09 Sep", "10 Oct", "11 Nov", "12 Dec"]
            st.bar_chart(season, height=200)
            st.caption("Tall bars = months this share was usually in demand. Watch for those.")
        else:
            st.info("Not enough history to chart yet.")


def detail(row, qty: int, owned: bool = False):
    if owned:
        action_box(row, qty)
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

    label, notes = options_lean(row["symbol"], row.get("coc"))
    if notes:
        st.markdown(
            f"""<div class="earn"><b>Options positioning: {label}</b><br>
            <span style="font-size:.85rem">{" · ".join(notes)}</span></div>""",
            unsafe_allow_html=True)
        tr = track_record(label)
        if tr:
            st.caption(tr)
        walls = strike_walls(row["symbol"])
        if len(walls) > 3:
            st.markdown("**Where the option bets sit (nearest expiry)**")
            st.bar_chart(walls, height=220, stack=False)
            st.caption("Open positions by strike price. Big put bars below the price often act "
                       "as support levels; big call bars above as resistance. Descriptive, not predictive.")

    h = fee_history(row["symbol"])
    if len(h) > 5:
        st.markdown("**Recent fee, with 5 & 20-day averages**")
        recent = h.tail(130).set_index("date").copy()
        recent["5-day avg"] = recent["fee"].rolling(5).mean()
        recent["20-day avg"] = recent["fee"].rolling(20).mean()
        st.line_chart(recent[["fee", "5-day avg", "20-day avg"]], height=230)

        if h["date"].dt.year.nunique() >= 2:
            st.markdown("**Which months this share usually pays**")
            years = sorted(h["date"].dt.year.unique(), reverse=True)
            pick = st.selectbox(
                "Show", ["Average of all years"] + [str(y) for y in years],
                key=f"season_{row['symbol']}", label_visibility="collapsed")
            hh = h if pick == "Average of all years" else h[h["date"].dt.year == int(pick)]
            season = hh.groupby(hh["date"].dt.month)["fee"].mean().reindex(range(1, 13))
            season.index = ["01 Jan", "02 Feb", "03 Mar", "04 Apr", "05 May", "06 Jun",
                            "07 Jul", "08 Aug", "09 Sep", "10 Oct", "11 Nov", "12 Dec"]
            st.bar_chart(season, height=200)
            st.caption("Average lending fee by month"
                       + ("" if pick == "Average of all years" else f" in {pick}")
                       + ". Tall bars = months this share is usually in demand. "
                         "Gaps = months with no lending activity.")

            st.markdown("**Coming months — what this share typically pays**")
            mnames = ["Jan", "Feb", "Mar", "Apr", "May", "Jun",
                      "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]
            bym = h.groupby(h["date"].dt.month)["fee"]
            med, lo, hi = bym.median(), bym.quantile(.25), bym.quantile(.75)
            this_m = pd.Timestamp.today().month
            months = [(this_m + i - 1) % 12 + 1 for i in range(1, 7)]
            proj = pd.DataFrame({
                "typical fee": [med.get(m) for m in months],
                "usual low": [lo.get(m) for m in months],
                "usual high": [hi.get(m) for m in months],
            }, index=[f"{i+1:02d} {mnames[m-1]}" for i, m in enumerate(months)])
            st.line_chart(proj, height=220)
            st.caption("Based on how this share behaved in these months over the last 5 years — "
                       "a pattern, not a promise. Actual fees depend on that month's demand "
                       "(dividends, shorting interest).")

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

try:
    pp = pd.read_sql_query(
        """SELECT date, stk_fut_long - stk_fut_short AS net FROM participant_oi
           WHERE category='FII' ORDER BY date DESC LIMIT 6""", con)
    if len(pp) >= 2:
        now, then = pp["net"].iloc[0], pp["net"].iloc[-1]
        word = "net long" if now > 0 else "net short"
        chg = "adding" if abs(now) > abs(then) and (now > 0) == (then > 0) else (
              "reducing" if (now > 0) == (then > 0) else "flipping")
        st.caption(f"Market context: big foreign funds (FII) are {word} stock futures "
                   f"({now/1000:,.0f}k contracts), {chg} over the past week.")
except Exception:
    pass

scores = pd.read_sql_query("SELECT * FROM scores WHERE date=?", con, params=(latest,))

@st.cache_data(ttl=86400)
def all_slb_symbols():
    return [r[0] for r in get_con().execute(
        "SELECT DISTINCT symbol FROM slb_trades ORDER BY symbol")]
smap = scores.set_index("symbol", drop=False)
portfolio = get_portfolio()
pins = get_pins()

# ---- sidebar: manage portfolio ----
with st.sidebar:
    st.subheader("My portfolio")
    all_syms = all_slb_symbols()
    add = st.selectbox("Add share", [""] + [s for s in all_syms if s not in portfolio],
                       help="Every SLB-eligible NSE stock. Quiet ones still appear — "
                            "open them to see which months they usually rent.")
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
    stock_row(smap.loc[s], portfolio.get(s, 0), s in pins, pins, key=f"my_{s}",
              owned=s in portfolio)
not_trading = [s for s in portfolio if s not in smap.index]
for s in sorted(not_trading):
    quiet_row(s, portfolio.get(s, 0))

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

with st.expander("How share lending works — one-time setup & each time"):
    st.markdown("""
**One-time setup (do this once):**
1. Ask your broker: *"Do you support SLBM share lending on NSE?"* Not every broker
   offers it — if yours doesn't, this is worth choosing a broker over.
2. They may ask you to sign a form to activate the SLB segment. That's it.

**Each time you lend (2-minute job):**
1. When a share shows 🟢 IN DEMAND here, open the broker's SLB section (or call your dealer).
2. Place a **LEND** order: stock name, quantity, and your minimum fee per share
   (this page shows today's fee and the recent range — ask near those).
3. If matched, the fee is credited by the next day. Your shares come back
   automatically at the end of the lending period — usually around the first
   Thursday of the next month.

**Good to know:**
- Your shares stay yours. The clearing corporation guarantees their return.
- Dividends during the lending period: the borrower pays you the same amount.
- Need the shares back early? Brokers allow early recall (small cost).
- The fee you earn is added to your income for tax — it's not a sale, so no
  capital-gains event. Confirm once with your CA.
""")

with st.expander("How to read this page"):
    st.markdown("""
- **Fee** — the rent someone pays you per share to borrow it for the current period (usually till month-end).
- **% / yr** — that fee converted to a yearly return rate on the share's price, so you can compare shares fairly.
- **In demand / Warming up / Quiet** — our 0–100 score from five signals: upcoming dividends, fee trend vs its averages, lending volume, borrowed positions building, and futures/options positioning (cost of carry, put-call ratio).
- **Months chart** — 5 years of history showing which calendar months this share usually pays. This answers "why was it hot last month and dead now."
- Signals, **not guarantees** — the fee is set by market auction every day. Lending happens through your broker's SLBM facility.
""")
