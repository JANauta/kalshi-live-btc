import math
from datetime import datetime, timezone

import pandas as pd
import plotly.graph_objects as go
import requests
import streamlit as st

st.set_page_config(page_title="BTC 15m Decision Monitor", page_icon="₿", layout="centered")
BASE = "https://external-api.kalshi.com/trade-api/v2"
COINBASE = "https://api.exchange.coinbase.com/products/BTC-USD"


def fetch(url, params=None):
    # Separate requests avoid sharing a mutable Session between cached calls.
    response = requests.get(url, params=params, timeout=(5), headers={"User-Agent": "BTC15mDecisionMonitor/1.1"})
    response.raise_for_status()
    return response.json()


@st.cache_data(ttl=10, show_spinner=False)
def discover_market():
    payload = fetch(BASE + "/markets", {"series_ticker": "KXBTC15M", "status": "open", "limit": 100})
    now = datetime.now(timezone.utc)
    candidates = []
    for market in payload.get("markets", []):
        try:
            closing = datetime.fromisoformat(market["close_time"].replace("Z", "+00:00"))
            if closing > now:
                candidates.append((closing, market["ticker"]))
        except (KeyError, TypeError, ValueError):
            continue
    return min(candidates)[1] if candidates else None


@st.cache_data(ttl=2, show_spinner=False)
def market_detail(ticker):
    return fetch(BASE + "/markets/" + ticker)["market"]


@st.cache_data(ttl=3, show_spinner=False)
def btc_ticker():
    return float(fetch(COINBASE + "/ticker")["price"])


@st.cache_data(ttl=60, show_spinner=False)
def btc_candles():
    raw = fetch(COINBASE + "/candles", {"granularity": 60})
    df = pd.DataFrame(raw, columns=["time", "low", "high", "open", "close", "volume"])
    if df.empty:
        return df
    df["time"] = pd.to_datetime(df["time"], unit="s", utc=True)
    for col in ("low", "high", "open", "close", "volume"):
        df[col] = pd.to_numeric(df[col], errors="coerce")
    return df.sort_values("time").tail(60).reset_index(drop=True)


def number(value):
    try:
        return float(value) if value is not None and value != "" else None
    except (TypeError, ValueError):
        return None


def probability_above(current, target, remaining_seconds, closes):
    """Uncalibrated illustration only; NOT a validated Kalshi win probability."""
    returns = (closes / closes.shift(1)).apply(
        lambda x: math.log(x) if pd.notna(x) and x > 0 else float("nan")
    ).dropna()
    if len(returns) < 20 or current <= 0 or target <= 0:
        return None, None
    minute_sigma = float(returns.tail(45).std())
    if not math.isfinite(minute_sigma) or minute_sigma <= 0:
        return None, None
    sigma = minute_sigma * math.sqrt(max(remaining_seconds, 1) / 60)
    z = math.log(current / target) / sigma
    p = 0.5 * (1 + math.erf(z / math.sqrt(2)))
    return max(0.001, min(0.999, p)), minute_sigma


st.title("₿ BTC 15m Decision Monitor")
st.caption("Manual trading only · Experimental final 2-minute analysis · No orders placed")

with st.expander("⚙️ Decision settings"):
    experimental = st.toggle("Enable experimental BUY recommendations", value=False,
                             help="Model is uncalibrated. Use for observation only, not verified trade signals.")
    min_edge = st.slider("Minimum illustrative edge", 0.05, 0.30, 0.15, 0.01)
    fee_allowance = st.slider("Fee/slippage planning buffer ($/contract)", 0.00, 0.10, 0.04, 0.01)
    max_ask = st.slider("Maximum contract ask ($)", 0.50, 0.99, 0.85, 0.01)

# One fragment handles quotes/decision at 3-second intervals. Chart is rendered
# separately below, so it is not reconstructed on every quote update.
@st.fragment(run_every="1s")
def live_monitor():
    now = datetime.now(timezone.utc)
    try:
        ticker = discover_market()
        if not ticker:
            st.warning("No open BTC 15-minute market found. Checking again shortly.")
            return
        market = market_detail(ticker)
        close_time = datetime.fromisoformat(market["close_time"].replace("Z", "+00:00"))
        seconds = max(0, int((close_time - now).total_seconds()))
        minutes, secs = divmod(seconds, 60)
        btc = btc_ticker()
        target = number(market.get("floor_strike"))
        up = number(market.get("yes_ask_dollars"))
        down = number(market.get("no_ask_dollars"))
        if up is None:
            cents = number(market.get("yes_ask"))
            up = cents / 100 if cents is not None else None
        if down is None:
            cents = number(market.get("no_ask"))
            down = cents / 100 if cents is not None else None

        c1, c2 = st.columns(2)
        c1.metric("BTC / USD (Coinbase)", f"${btc:,.2f}")
        c2.metric("Time remaining", f"{minutes:02d}:{secs:02d}")
        if target is None:
            st.warning("Kalshi target unavailable")
        else:
            st.metric("Kalshi target", f"${target:,.2f}", delta=f"${btc - target:+,.2f} Coinbase vs target")
        c3, c4 = st.columns(2)
        c3.metric("UP ask", f"{up:.0%}" if up is not None else "Unavailable")
        c4.metric("DOWN ask", f"{down:.0%}" if down is not None else "Unavailable")

        if seconds > 120:
            st.info("⏳ BOT STATUS: WAITING — starts at 02:00")
        elif seconds >= 30:
            st.success("🔍 BOT STATUS: ANALYZING")
        else:
            st.warning("⛔ BOT STATUS: ENTRY WINDOW CLOSED")

        decision = "SKIP"
        explanation = ""
        p_up, minute_sigma = None, None
        try:
            candles = btc_candles()
            if target is not None and not candles.empty:
                p_up, minute_sigma = probability_above(btc, target, seconds, candles["close"])
        except (requests.RequestException, ValueError, KeyError) as err:
            st.caption(f"Volatility feed unavailable: {err}")

        if seconds == 0:
            explanation = "Market closing or closed."
        elif seconds > 120:
            explanation = "Monitoring. Analysis starts at 02:00 remaining."
        elif seconds < 30:
            explanation = "No new entries below 30 seconds."
        elif target is None or p_up is None or up is None or down is None:
            explanation = "Missing target, quotes, or sufficient candle history."
        elif not experimental:
            explanation = "Experimental recommendations are OFF in settings."
        else:
            candidates = [
                ("BUY UP", p_up - up - fee_allowance, up),
                ("BUY DOWN", (1 - p_up) - down - fee_allowance, down),
            ]
            best = max(candidates, key=lambda x: x[1])
            if best[1] >= min_edge and best[2] <= max_ask:
                decision = best[0]
                explanation = f"Illustrative model edge: {best[1]:+.1%}. NOT validated against Kalshi outcomes."
            else:
                explanation = "Neither side passes the illustrative edge and price filters."

        st.subheader("Final 2-minute decision")
        if decision == "SKIP":
            st.warning("⏸️ SKIP")
        else:
            st.success(f"🧪 {decision} — EXPERIMENTAL ONLY")
        st.write(explanation)
        if p_up is not None:
            st.caption(f"Uncalibrated estimate: UP {p_up:.1%} · DOWN {1-p_up:.1%}")
        if minute_sigma is not None:
            st.caption(f"Recent 1-minute volatility: {minute_sigma:.3%}")
        st.caption("Coinbase spot is NOT Kalshi's official settlement benchmark.")
        st.caption(f"Last updated {now.strftime('%H:%M:%S')} UTC · quotes cached 3s")
        with st.expander("Market information"):
            st.write("Ticker:", ticker)
            st.write("Title:", market.get("title"))
            st.write("Rules:", market.get("rules_primary"))
    except (requests.RequestException, KeyError, ValueError, TypeError) as err:
        st.error(f"Live feed temporarily unavailable: {err}")
        st.caption("The dashboard will retry. No orders are placed.")


live_monitor()

# Render chart only on full page reruns, not every 3-second fragment rerun.
with st.expander("📈 BTC chart (open to view)"):
    try:
        chart_data = btc_candles()
        if chart_data.empty:
            st.info("Chart unavailable.")
        else:
            fig = go.Figure()
            fig.add_trace(go.Scatter(x=chart_data["time"], y=chart_data["close"], mode="lines", name="Coinbase BTC"))
            fig.update_layout(height=320, margin=dict(l=5, r=5, t=20, b=5), xaxis_title="UTC", yaxis_title="USD")
            st.plotly_chart(fig, use_container_width=True)
            st.caption("Chart refreshes on page rerun; quote panel refreshes automatically.")
    except (requests.RequestException, ValueError, KeyError) as err:
        st.info(f"Chart unavailable: {err}")
