import math
from datetime import datetime, timezone

import pandas as pd
import plotly.graph_objects as go
import requests
import streamlit as st

st.set_page_config(page_title="BTC 15m Decision Monitor", page_icon="₿", layout="centered")
BASE = "https://external-api.kalshi.com/trade-api/v2"
COINBASE = "https://api.exchange.coinbase.com/products/BTC-USD"
SESSION = requests.Session()
SESSION.headers.update({"User-Agent": "BTC15mManualDecisionDashboard/1.0"})


def fetch(url, params=None):
    response = SESSION.get(url, params=params, timeout=8)
    response.raise_for_status()
    return response.json()


@st.cache_data(ttl=10, show_spinner=False)
def discover_market():
    payload = fetch(BASE + "/markets", {"series_ticker": "KXBTC15M", "status": "open", "limit": 100})
    now = datetime.now(timezone.utc)
    available = []
    for m in payload.get("markets", []):
        try:
            close = datetime.fromisoformat(m["close_time"].replace("Z", "+00:00"))
            if close > now:
                available.append((close, m["ticker"]))
        except (KeyError, TypeError, ValueError):
            pass
    return min(available)[1] if available else None


@st.cache_data(ttl=2, show_spinner=False)
def market_detail(ticker):
    return fetch(BASE + "/markets/" + ticker)["market"]


@st.cache_data(ttl=2, show_spinner=False)
def btc_ticker():
    return float(fetch(COINBASE + "/ticker")["price"])


@st.cache_data(ttl=30, show_spinner=False)
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
    except (ValueError, TypeError):
        return None


def probability_above(current, target, remaining_seconds, closes):
    """Uncalibrated illustrative lognormal approximation, NOT a trading probability."""
    returns = (closes / closes.shift(1)).apply(lambda x: math.log(x) if pd.notna(x) and x > 0 else float("nan")).dropna()
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
st.caption("Manual trading only · Final 2-minute analysis · No orders placed")
with st.expander("⚙️ Decision settings", expanded=False):
    experimental = st.toggle("Enable experimental BUY recommendations", value=False,
                             help="Off by default because the probability approximation is not calibrated to Kalshi settlements.")
    min_edge = st.slider("Minimum estimated edge after fee allowance", 0.05, 0.30, 0.15, 0.01)
    fee_allowance = st.slider("Conservative fee/slippage allowance per contract ($)", 0.00, 0.10, 0.04, 0.01)
    max_ask = st.slider("Maximum contract ask ($)", 0.50, 0.99, 0.85, 0.01)
    st.caption("Fees depend on contract and execution. Allowance is a planning buffer, not a verified fee quote.")


@st.fragment(run_every="1s")
def dashboard():
    now = datetime.now(timezone.utc)
    try:
        ticker = discover_market()
        if not ticker:
            st.warning("No open BTC 15-minute market found. Waiting for the next market.")
            st.caption("Market discovery is cached for 10 seconds.")
            return
        market = market_detail(ticker)
        close_time = datetime.fromisoformat(market["close_time"].replace("Z", "+00:00"))
        seconds = max(0, int((close_time - now).total_seconds()))
        mm, ss = divmod(seconds, 60)
        if seconds > 120:
    st.info("⏳ BOT STATUS: WAITING")
elif seconds >= 30:
    st.success("🔍 BOT STATUS: ANALYZING")
else:
    st.warning("⛔ BOT STATUS: ENTRY WINDOW CLOSED")
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
        c2.metric("Time remaining", f"{mm:02d}:{ss:02d}")
        if target is not None:
            st.metric("Kalshi target", f"${target:,.2f}", delta=f"${btc-target:+,.2f} Coinbase vs target")
        else:
            st.warning("Kalshi target unavailable. Cannot estimate direction.")
        c3, c4 = st.columns(2)
        c3.metric("UP ask", f"{up:.0%}" if up is not None else "Unavailable")
        c4.metric("DOWN ask", f"{down:.0%}" if down is not None else "Unavailable")

        decision, explanation = "SKIP", ""
        p_up, minute_sigma = None, None
        try:
            candles = btc_candles()
            if target is not None and not candles.empty:
                p_up, minute_sigma = probability_above(btc, target, seconds, candles["close"])
        except (requests.RequestException, ValueError, KeyError) as err:
            candles = pd.DataFrame()
            st.caption(f"Chart/volatility temporarily unavailable: {err}")

        if seconds == 0:
            explanation = "Market closing or closed. Wait for the next contract."
        elif seconds > 120:
            explanation = "Monitoring. Decision window begins at 02:00 remaining."
        elif seconds < 30:
            explanation = "Less than 30 seconds remain: no new entry recommended."
        elif target is None or p_up is None or up is None or down is None:
            explanation = "Missing price, target, or sufficient volatility history."
        elif not experimental:
            explanation = "Experimental recommendations disabled. Enable them in Settings only for evaluation."
        else:
            candidates = [
                ("BUY UP", p_up - up - fee_allowance, up),
                ("BUY DOWN", (1 - p_up) - down - fee_allowance, down),
            ]
            best = max(candidates, key=lambda x: x[1])
            if best[1] >= min_edge and best[2] <= max_ask:
                decision = best[0]
                explanation = f"Experimental model edge {best[1]:+.1%} after fee/slippage allowance. Not validated."
            else:
                explanation = "Neither side passes the configured edge and price filters."

        st.divider()
        st.subheader("Final 2-minute decision")
        if decision == "SKIP":
            st.warning("⏸️ SKIP")
        else:
            st.success(f"🧪 {decision} — EXPERIMENTAL, NOT VALIDATED")
        st.write(explanation)
        if p_up is not None:
            st.caption(f"Uncalibrated model estimate: UP {p_up:.1%} · DOWN {1-p_up:.1%}. Not a reliable win probability.")
        if minute_sigma is not None:
            st.caption(f"Recent 1-minute log-return volatility: {minute_sigma:.3%}")
        st.caption("Coinbase spot is a proxy; Kalshi settlement uses its specified benchmark. Late trades can lose despite a large Coinbase distance.")

        with st.expander("📈 BTC chart and Kalshi target"):
            if not candles.empty:
                fig = go.Figure()
                fig.add_trace(go.Scatter(x=candles["time"], y=candles["close"], mode="lines", name="Coinbase BTC"))
                if target is not None:
                    fig.add_hline(y=target, line_dash="dash", line_color="red", annotation_text="Kalshi target")
                fig.update_layout(height=330, margin=dict(l=5, r=5, t=20, b=5),
                                  xaxis_title="UTC", yaxis_title="USD")
                st.plotly_chart(fig, use_container_width=True)
            else:
                st.info("Chart unavailable right now.")
        with st.expander("Market information and limitations"):
            st.write("Ticker:", ticker)
            st.write("Title:", market.get("title"))
            st.write("Rules:", market.get("rules_primary"))
            st.write("Last screen refresh:", now.strftime("%H:%M:%S UTC"))
            st.info("Quote data cached 2 seconds, candles 30 seconds, market discovery 10 seconds. A 1-second screen rerun does not mean 1-second market data updates.")
    except (requests.RequestException, KeyError, ValueError, TypeError) as err:
        st.error(f"Data feed error: {err}")
        st.info("The monitor will retry automatically. No trade has been placed.")


dashboard()
