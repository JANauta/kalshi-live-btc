import math
from datetime import datetime, timezone

import pandas as pd
import plotly.graph_objects as go
import requests
import streamlit as st

st.set_page_config(page_title="BTC 15m Decision Monitor", page_icon="₿", layout="centered")
BASE = "https://external-api.kalshi.com/trade-api/v2"
COINBASE = "https://api.exchange.coinbase.com/products/BTC-USD"
HEADERS = {"User-Agent": "BTC15mDecisionMonitor/2.0"}
SIGNAL_START = 150  # seconds remaining
MIN_ASK = 0.70


def fetch(url, params=None):
    response = requests.get(url, params=params, headers=HEADERS, timeout=5)
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
        result = float(value)
        return result if math.isfinite(result) else None
    except (TypeError, ValueError):
        return None


def ask_price(market, side):
    dollars = number(market.get(f"{side}_ask_dollars"))
    if dollars is not None:
        return dollars
    cents = number(market.get(f"{side}_ask"))
    return cents / 100 if cents is not None else None


def probability_above(current, target, remaining_seconds, closes):
    """Uncalibrated illustration, NOT a verified win probability."""
    returns = (closes / closes.shift(1)).apply(
        lambda x: math.log(x) if pd.notna(x) and x > 0 else float("nan")
    ).dropna()
    if len(returns) < 20 or current <= 0 or target <= 0:
        return None
    sigma_per_minute = float(returns.tail(45).std())
    if not math.isfinite(sigma_per_minute) or sigma_per_minute <= 0:
        return None
    sigma = sigma_per_minute * math.sqrt(max(remaining_seconds, 1) / 60)
    z = math.log(current / target) / sigma
    return max(0.001, min(0.999, 0.5 * (1 + math.erf(z / math.sqrt(2)))))


st.title("₿ BTC 15m Decision Monitor")
st.caption("Manual monitoring · Analysis throughout the market · Experimental signals only")
st.caption("Signal window: 02:30 remaining to market close · Minimum qualifying ask: 70¢")


@st.fragment(run_every="1s")
def live_monitor():
    now = datetime.now(timezone.utc)
    try:
        ticker = discover_market()
        if not ticker:
            st.warning("No open BTC 15-minute market found. Retrying shortly.")
            return
        market = market_detail(ticker)
        closing = datetime.fromisoformat(market["close_time"].replace("Z", "+00:00"))
        seconds = max(0, int((closing - now).total_seconds()))
        minutes, secs = divmod(seconds, 60)
        btc = btc_ticker()
        target = number(market.get("floor_strike"))
        up = ask_price(market, "yes")
        down = ask_price(market, "no")

        a, b = st.columns(2)
        a.metric("BTC / USD (Coinbase)", f"${btc:,.2f}")
        b.metric("Time remaining", f"{minutes:02d}:{secs:02d}")
        if target is None:
            st.warning("Kalshi target unavailable")
        else:
            st.metric("Kalshi target", f"${target:,.2f}", delta=f"${btc - target:+,.2f} Coinbase vs target")
        a, b = st.columns(2)
        a.metric("UP ask", f"{up:.0%}" if up is not None else "Unavailable")
        b.metric("DOWN ask", f"{down:.0%}" if down is not None else "Unavailable")

        p_up = None
        try:
            candles = btc_candles()
            if target is not None and not candles.empty:
                p_up = probability_above(btc, target, seconds, candles["close"])
        except (requests.RequestException, ValueError, KeyError, TypeError) as err:
            st.caption(f"Volatility feed unavailable: {err}")

        if seconds > SIGNAL_START:
            st.info("🔎 BOT STATUS: ANALYZING — signal window opens at 02:30")
        elif seconds > 0:
            st.success("🔍 BOT STATUS: SIGNAL WINDOW OPEN")
        else:
            st.warning("⛔ BOT STATUS: MARKET CLOSING")

        decision = "SKIP"
        reason = ""
        if seconds == 0:
            reason = "Market closing or closed. Wait for the next contract."
        elif seconds > SIGNAL_START:
            reason = "Monitoring BTC and Kalshi continuously. No signal before 02:30."
        elif target is None or p_up is None or up is None or down is None:
            reason = "Waiting for target, quotes, and sufficient volatility data."
        else:
            # A 70¢ ask is a price filter, NOT a 70% win probability.
            # Require model direction agreement; model itself is not calibrated.
            candidates = []
            if up >= MIN_ASK and p_up > 0.5:
                candidates.append(("UP", up, p_up))
            if down >= MIN_ASK and (1 - p_up) > 0.5:
                candidates.append(("DOWN", down, 1 - p_up))
            if candidates:
                side, ask, estimate = max(candidates, key=lambda x: x[2] - x[1])
                decision = f"BUY {side}"
                reason = (f"{side} ask is {ask:.0%} (meets 70¢ threshold), and the "
                          f"uncalibrated volatility model favors {side} ({estimate:.1%}). "
                          "This does NOT establish positive expected value.")
            else:
                reason = "No side has both an ask of at least 70¢ and matching model direction."

        st.subheader("150-second decision window")
        if decision == "SKIP":
            st.warning("⏸️ SKIP")
        else:
            st.success(f"🧪 {decision} — EXPERIMENTAL, NOT VALIDATED")
        st.write(reason)
        if p_up is not None:
            st.caption(f"Uncalibrated model: UP {p_up:.1%} · DOWN {1 - p_up:.1%}")
        st.caption("Coinbase is not Kalshi's official settlement benchmark. Prices and model estimates can disagree.")
        st.caption(f"Last checked: {now:%H:%M:%S} UTC · quotes cached 2s · candles cached 30s")
        with st.expander("Market information"):
            st.write("Ticker:", ticker)
            st.write("Title:", market.get("title"))
            st.write("Rules:", market.get("rules_primary"))
    except (requests.RequestException, KeyError, ValueError, TypeError) as err:
        st.error(f"Live feed temporarily unavailable: {err}")
        st.caption("Will retry automatically. No orders are placed.")


live_monitor()

# Intentionally outside the 1-second fragment to avoid replotting every second.
with st.expander("📈 BTC chart and Kalshi target (updates on page rerun)"):
    try:
        chart_data = btc_candles()
        if chart_data.empty:
            st.info("Chart unavailable.")
        else:
            fig = go.Figure()
            fig.add_trace(go.Scatter(
                x=chart_data["time"], y=chart_data["close"],
                mode="lines", name="Coinbase BTC"
            ))
            ticker = discover_market()
            if ticker:
                chart_market = market_detail(ticker)
                chart_target = number(chart_market.get("floor_strike"))
                if chart_target is not None:
                    fig.add_hline(
                        y=chart_target, line_dash="dash", line_color="red",
                        annotation_text=f"Kalshi target ${chart_target:,.2f}",
                        annotation_position="top left"
                    )
            fig.update_layout(height=320, margin=dict(l=5, r=5, t=20, b=5),
                              xaxis_title="UTC", yaxis_title="USD")
            st.plotly_chart(fig, use_container_width=True)
            st.caption("Chart and target line refresh when the page reruns, not every second.")
    except (requests.RequestException, ValueError, KeyError, TypeError) as err:
        st.info(f"Chart unavailable: {err}")
