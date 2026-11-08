
from datetime import datetime, timezone
from io import StringIO

import pandas as pd
import plotly.graph_objects as go
import requests
import streamlit as st

st.set_page_config(page_title="Kalshi Multi-Market", page_icon="📈", layout="centered")
API = "https://external-api.kalshi.com/trade-api/v2"
COINBASE = "https://api.exchange.coinbase.com/products"
MARKETS = {
    "₿ Bitcoin": {"series": "KXBTC15M", "product": "BTC-USD"},
    "Ξ Ethereum": {"series": "KXETH15M", "product": "ETH-USD"},
    "◎ Solana": {"series": "KXSOL15M", "product": "SOL-USD"},
    "🥇 Gold": {"series": "", "product": None},
}


def get_json(url, params=None):
    response = requests.get(url, params=params, timeout=8, headers={"User-Agent": "KalshiMobileDashboard/1.0"})
    response.raise_for_status()
    return response.json()


@st.cache_data(ttl=5, show_spinner=False)
def find_market(series, gold=False):
    now = datetime.now(timezone.utc)
    if gold and not series:
        # Discover rather than assume a particular Gold series ticker exists.
        data = get_json(API + "/markets", {"status": "open", "limit": 1000})
        markets = [m for m in data.get("markets", []) if "gold" in (m.get("title", "") + " " + m.get("subtitle", "")).lower()]
        # Gold contracts must explicitly describe a 15-minute market.
        markets = [m for m in markets if any(k in (m.get("title", "") + " " + m.get("subtitle", "") + " " + m.get("series_ticker", "")).lower() for k in ("15 min", "15-min", "15m", "15 minute"))]
    else:
        if not series:
            return None
        data = get_json(API + "/markets", {"series_ticker": series, "status": "open", "limit": 100})
        markets = data.get("markets", [])
    candidates = []
    for m in markets:
        try:
            close = datetime.fromisoformat(m["close_time"].replace("Z", "+00:00"))
            if close > now:
                candidates.append((close, m))
        except (KeyError, TypeError, ValueError):
            continue
    return min(candidates, key=lambda x: x[0])[1] if candidates else None


@st.cache_data(ttl=2, show_spinner=False)
def market_detail(ticker):
    return get_json(API + "/markets/" + ticker)["market"]


@st.cache_data(ttl=2, show_spinner=False)
def coinbase_ticker(product):
    return float(get_json(f"{COINBASE}/{product}/ticker")["price"])


@st.cache_data(ttl=30, show_spinner=False)
def coinbase_candles(product):
    candles = get_json(f"{COINBASE}/{product}/candles", {"granularity": 60})
    df = pd.DataFrame(candles, columns=["time", "low", "high", "open", "close", "volume"])
    df["time"] = pd.to_datetime(df["time"], unit="s", utc=True)
    return df.sort_values("time").tail(30)


def dollars(value):
    try:
        return float(value) if value is not None and value != "" else None
    except (TypeError, ValueError):
        return None


def fmt_cents(value):
    return f"{value * 100:.1f}¢" if value is not None else "—"


st.title("📈 Kalshi Multi-Market")
st.caption("Mobile monitor · Manual decisions · Real-trade tracking")
selected = st.selectbox("Choose market", list(MARKETS))
config = MARKETS[selected]
refresh = st.select_slider("Refresh interval (seconds)", options=[2, 5, 10], value=2)
if selected == "🥇 Gold":
    gold_series = st.text_input("Gold 15-minute series ticker (optional)", placeholder="Enter a verified Kalshi series ticker")
    st.caption("Without a ticker, the app searches the first page of open markets for explicitly labeled Gold 15-minute contracts. Results may be incomplete.")
else:
    gold_series = ""


@st.fragment(run_every=f"{refresh}s")
def live():
    now = datetime.now(timezone.utc)
    st.caption("Updated: " + now.strftime("%H:%M:%S UTC"))
    try:
        found = find_market(gold_series if selected == "🥇 Gold" else config["series"], gold=(selected == "🥇 Gold"))
        if not found:
            st.warning("No matching open 15-minute contract found. Gold may require its verified series ticker; market availability is not guaranteed.")
            return
        m = market_detail(found["ticker"])
    except requests.RequestException as exc:
        st.error(f"Kalshi connection error: {exc}")
        return
    except (KeyError, ValueError, TypeError) as exc:
        st.error(f"Kalshi returned unexpected market data: {exc}")
        return

    st.subheader(m.get("title") or selected)
    st.caption(f"Ticker: {m['ticker']} · Status: {m.get('status', 'unknown')}")
    try:
        close = datetime.fromisoformat(m["close_time"].replace("Z", "+00:00"))
        remaining = max(0, int((close - now).total_seconds()))
        st.metric("Time until market close", f"{remaining // 60:02d}:{remaining % 60:02d}")
    except (KeyError, TypeError, ValueError):
        st.metric("Time until market close", "—")

    yes_ask = dollars(m.get("yes_ask_dollars"))
    no_ask = dollars(m.get("no_ask_dollars"))
    yes_bid = dollars(m.get("yes_bid_dollars"))
    no_bid = dollars(m.get("no_bid_dollars"))
    up, down = st.columns(2)
    with up:
        st.metric("UP / YES ask", fmt_cents(yes_ask))
        st.caption("Bid: " + fmt_cents(yes_bid))
    with down:
        st.metric("DOWN / NO ask", fmt_cents(no_ask))
        st.caption("Bid: " + fmt_cents(no_bid))

    target = dollars(m.get("floor_strike"))
    if config["product"]:
        try:
            spot = coinbase_ticker(config["product"])
            st.metric("Coinbase spot (indicative)", f"${spot:,.2f}")
            if target is not None:
                st.metric("Spot minus Kalshi target", f"${spot - target:+,.2f}")
            df = coinbase_candles(config["product"])
            fig = go.Figure()
            fig.add_trace(go.Scatter(x=df["time"], y=df["close"], mode="lines", name="Coinbase spot", line={"color": "#16a34a", "width": 2}))
            if target is not None:
                fig.add_hline(y=target, line_dash="dash", line_color="#ef4444", annotation_text="Kalshi target")
            fig.update_layout(height=300, margin=dict(l=10, r=10, t=20, b=10), xaxis_title=None, yaxis_title="USD", hovermode="x unified")
            st.plotly_chart(fig, use_container_width=True)
        except (requests.RequestException, KeyError, ValueError, TypeError) as exc:
            st.warning(f"Coinbase price/chart temporarily unavailable: {exc}")
    else:
        st.info("Gold chart omitted until a reliable, settlement-compatible Gold price feed is configured.")
        if target is not None:
            st.metric("Kalshi target", f"${target:,.2f}")

    with st.expander("Contract rules and details"):
        st.write("Rules:", m.get("rules_primary", "Unavailable"))
        st.write("Strike:", m.get("floor_strike", "Unavailable"))
        st.write("Volume:", m.get("volume_fp", "Unavailable"))
        st.write("Close time:", m.get("close_time", "Unavailable"))

    st.info("Signal: SKIP until a validated probability model demonstrates positive expected value after fees. Coinbase spot is not the official Kalshi settlement reference.")

live()


# Records are stored in the browser session, not permanently on Streamlit Cloud.
# Export a CSV to keep them and import it when returning.
TRADE_COLUMNS = ["timestamp_utc", "market", "ticker", "side", "contracts", "entry_price", "entry_fees", "exit_price", "exit_fees", "status", "pnl"]
if "trades" not in st.session_state:
    st.session_state.trades = pd.DataFrame(columns=TRADE_COLUMNS)


def compute_pnl(contracts, entry_price, entry_fees, exit_price, exit_fees, status):
    if status == "Open":
        return None
    if status == "Won":
        proceeds = contracts
    elif status == "Lost":
        proceeds = 0.0
    else:  # Sold early: actual sale proceeds per contract
        proceeds = contracts * exit_price
    return round(proceeds - contracts * entry_price - entry_fees - exit_fees, 2)


st.divider()
st.header("💵 Real-trade profit tracker")
st.caption("Manual records only. This dashboard does not connect to your Kalshi account or place orders. Export your CSV to preserve your records.")

with st.expander("Import saved trade history"):
    uploaded = st.file_uploader("Choose your previously exported CSV", type="csv", key="trade_upload")
    if uploaded is not None and st.button("Import CSV (replaces current session records)"):
        try:
            imported = pd.read_csv(uploaded)
            if not set(TRADE_COLUMNS).issubset(imported.columns):
                st.error("CSV is missing required tracker columns. Please import a CSV exported by this dashboard.")
            else:
                imported = imported[TRADE_COLUMNS].copy()
                for col in ["contracts", "entry_price", "entry_fees", "exit_price", "exit_fees", "pnl"]:
                    imported[col] = pd.to_numeric(imported[col], errors="coerce")
                st.session_state.trades = imported
                st.success(f"Imported {len(imported)} trades.")
        except Exception as exc:
            st.error(f"Unable to import file: {exc}")

with st.form("add_real_trade", clear_on_submit=True):
    st.subheader("Record a trade")
    asset = st.selectbox("Market", ["BTC", "ETH", "SOL", "Gold"])
    ticker_input = st.text_input("Kalshi contract ticker (optional)", placeholder="e.g. KXBTC15M-...")
    side_input = st.radio("Position", ["UP / YES", "DOWN / NO"], horizontal=True)
    contracts_input = st.number_input("Number of contracts", min_value=1, value=1, step=1)
    entry_cents = st.number_input("Actual purchase price per contract (¢)", min_value=0.01, max_value=99.99, value=50.0, step=1.0)
    entry_fee_input = st.number_input("Total purchase fees ($)", min_value=0.0, value=0.0, step=0.01)
    status_input = st.selectbox("Outcome", ["Open", "Won", "Lost", "Sold early"])
    if status_input == "Sold early":
        exit_cents = st.number_input("Actual sale price per contract (¢)", min_value=0.0, max_value=100.0, value=50.0, step=1.0)
    else:
        exit_cents = 0.0
    exit_fee_input = st.number_input("Total sale / settlement fees ($)", min_value=0.0, value=0.0, step=0.01)
    submitted = st.form_submit_button("Save trade", use_container_width=True)
    if submitted:
        pnl = compute_pnl(int(contracts_input), entry_cents / 100, entry_fee_input, exit_cents / 100, exit_fee_input, status_input)
        row = {"timestamp_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
               "market": asset, "ticker": ticker_input.strip(), "side": side_input,
               "contracts": int(contracts_input), "entry_price": round(entry_cents / 100, 4),
               "entry_fees": round(entry_fee_input, 2), "exit_price": round(exit_cents / 100, 4),
               "exit_fees": round(exit_fee_input, 2), "status": status_input, "pnl": pnl}
        st.session_state.trades = pd.concat([st.session_state.trades, pd.DataFrame([row])], ignore_index=True)
        st.success("Trade recorded. Export your CSV to keep it safely.")

trades = st.session_state.trades
if not trades.empty:
    closed = trades[trades["status"] != "Open"].copy()
    closed["pnl"] = pd.to_numeric(closed["pnl"], errors="coerce")
    closed = closed.dropna(subset=["pnl"])
    total_pnl = float(closed["pnl"].sum())
    wins = int((closed["pnl"] > 0).sum())
    win_rate = wins / len(closed) * 100 if len(closed) else 0
    total_spent = (pd.to_numeric(closed["contracts"]) * pd.to_numeric(closed["entry_price"]) + pd.to_numeric(closed["entry_fees"])).sum()
    roi = total_pnl / total_spent * 100 if total_spent else 0
    c1, c2 = st.columns(2)
    c1.metric("Realized net P&L", f"${total_pnl:+,.2f}")
    c2.metric("Profitable trades", f"{win_rate:.1f}%")
    c3, c4 = st.columns(2)
    c3.metric("Closed trades", len(closed))
    c4.metric("ROI on entry costs", f"{roi:+.1f}%")
    if len(closed):
        by_market = closed.groupby("market", as_index=False)["pnl"].sum().rename(columns={"pnl": "Net P&L ($)"})
        st.subheader("Profit by market")
        st.dataframe(by_market, hide_index=True, use_container_width=True)
    st.subheader("Your trade history")
    st.dataframe(trades.iloc[::-1], hide_index=True, use_container_width=True)
    st.download_button("⬇️ Export all trades as CSV", trades.to_csv(index=False).encode("utf-8"), file_name="kalshi_real_trades.csv", mime="text/csv", use_container_width=True)
    with st.expander("Remove a trade"):
        row_number = st.number_input("Trade row number (1 = oldest)", min_value=1, max_value=len(trades), value=len(trades), step=1)
        if st.button("Delete selected trade"):
            st.session_state.trades = trades.drop(trades.index[row_number - 1]).reset_index(drop=True)
            st.rerun()
else:
    st.info("No real trades recorded yet. Add your first trade above.")

st.caption("P&L uses your entered fills and fees, not live bid/ask estimates. An early sale is treated as closed. Open trades are excluded from realized P&L. Back up your CSV; Streamlit session data is temporary.")
