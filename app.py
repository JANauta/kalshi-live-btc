import streamlit as st
import requests
import pandas as pd
import plotly.graph_objects as go
from datetime import datetime, timezone

st.set_page_config(page_title="Kalshi BTC Live", page_icon="₿", layout="centered", initial_sidebar_state="collapsed")
BASE = "https://external-api.kalshi.com/trade-api/v2"
COINBASE = "https://api.exchange.coinbase.com/products/BTC-USD"

st.markdown("""<style>
.block-container {padding-top: 1rem; padding-bottom: 2rem; max-width: 760px;}
[data-testid="stMetricValue"] {font-size: clamp(1.35rem, 6vw, 2.1rem);}
[data-testid="stMetricLabel"] {font-size: .9rem;}
@media(max-width: 640px) {
 .block-container {padding-left: .8rem; padding-right: .8rem;}
 h1 {font-size: 1.6rem !important;}
 h2,h3 {font-size: 1.15rem !important;}
 [data-testid="stHorizontalBlock"] {gap: .45rem;}
 [data-testid="stMetric"] {padding: .5rem; border: 1px solid rgba(128,128,128,.22); border-radius: .7rem;}
}
</style>""", unsafe_allow_html=True)

st.title("₿ BTC 15-Minute Monitor")
st.caption("Kalshi market quotes + Coinbase price reference • Manual trading only")

@st.cache_data(ttl=2, show_spinner=False)
def fetch_json(url, params=None):
    response = requests.get(url, params=params, timeout=8, headers={"User-Agent": "KalshiBTCMonitor/1.0"})
    response.raise_for_status()
    return response.json()

@st.cache_data(ttl=30, show_spinner=False)
def fetch_candles():
    response = requests.get(f"{COINBASE}/candles", params={"granularity": 60}, timeout=8,
                            headers={"User-Agent": "KalshiBTCMonitor/1.0"})
    response.raise_for_status()
    return response.json()

@st.cache_data(ttl=5, show_spinner=False)
def find_active_market():
    data = fetch_json(BASE + "/markets", {"series_ticker": "KXBTC15M", "status": "open", "limit": 100})
    now = datetime.now(timezone.utc)
    candidates = []
    for market in data.get("markets", []):
        close_text = market.get("close_time")
        if not close_text:
            continue
        try:
            close = datetime.fromisoformat(close_text.replace("Z", "+00:00"))
        except ValueError:
            continue
        if close > now:
            candidates.append((close, market["ticker"]))
    return min(candidates)[1] if candidates else None

def parse_money(value):
    try:
        return float(value) if value is not None and value != "" else None
    except (ValueError, TypeError):
        return None

def quote_text(value):
    return f"{value * 100:.1f}¢" if value is not None else "—"

@st.fragment(run_every="1s")
def dashboard():
    now = datetime.now(timezone.utc)
    ticker = None
    market = None
    price = None
    errors = []
    try:
        price = float(fetch_json(f"{COINBASE}/ticker")["price"])
    except (requests.RequestException, KeyError, ValueError, TypeError) as exc:
        errors.append(f"Coinbase price unavailable: {exc}")
    try:
        ticker = find_active_market()
        if ticker:
            market = fetch_json(f"{BASE}/markets/{ticker}")["market"]
    except (requests.RequestException, KeyError, ValueError, TypeError) as exc:
        errors.append(f"Kalshi market unavailable: {exc}")

    st.metric("Coinbase BTC/USD", f"${price:,.2f}" if price is not None else "Unavailable")
    if market is None:
        st.warning("No active Kalshi BTC 15-minute market found right now.")
    else:
        close = datetime.fromisoformat(market["close_time"].replace("Z", "+00:00"))
        seconds = max(0, int((close - now).total_seconds()))
        mins, secs = divmod(seconds, 60)
        st.metric("Time until market closes", f"{mins:02d}:{secs:02d}")
        yes_ask = parse_money(market.get("yes_ask_dollars"))
        no_ask = parse_money(market.get("no_ask_dollars"))
        yes_bid = parse_money(market.get("yes_bid_dollars"))
        no_bid = parse_money(market.get("no_bid_dollars"))
        up, down = st.columns(2)
        with up:
            st.markdown("**🟢 UP / YES**")
            st.metric("Buy (ask)", quote_text(yes_ask))
            st.caption(f"Sell (bid): {quote_text(yes_bid)}")
        with down:
            st.markdown("**🔴 DOWN / NO**")
            st.metric("Buy (ask)", quote_text(no_ask))
            st.caption(f"Sell (bid): {quote_text(no_bid)}")

        target = parse_money(market.get("floor_strike"))
        if target is not None:
            st.metric("Kalshi price to beat", f"${target:,.2f}")
            if price is not None:
                difference = price - target
                st.metric("Coinbase vs. target", f"${difference:+,.2f}")
                st.caption("Coinbase is a reference price, not necessarily Kalshi's settlement source.")
            with st.expander("📈 BTC chart vs. Kalshi target", expanded=True):
                try:
                    candles = fetch_candles()
                    df = pd.DataFrame(candles, columns=["time", "low", "high", "open", "close", "volume"])
                    df["time"] = pd.to_datetime(df["time"], unit="s", utc=True)
                    df = df.sort_values("time").tail(30)
                    fig = go.Figure()
                    fig.add_trace(go.Scatter(x=df["time"], y=df["close"], mode="lines", name="Coinbase BTC", line=dict(color="#16a34a", width=2)))
                    fig.add_hline(y=target, line_color="#ef4444", line_dash="dash")
                    fig.update_layout(height=300, margin=dict(l=4, r=4, t=12, b=4),
                                      xaxis_title=None, yaxis_title="BTC/USD", hovermode="x unified",
                                      legend=dict(orientation="h", y=1.05))
                    st.plotly_chart(fig, use_container_width=True, config={"displayModeBar": False})
                    st.caption(f"Red dashed line: Kalshi target ${target:,.2f}. Historical candles update about every 30 seconds.")
                except (requests.RequestException, ValueError, KeyError) as exc:
                    st.warning(f"Chart temporarily unavailable: {exc}")
        else:
            st.info("Kalshi target price not available for this market.")

        with st.expander("Market details and rules"):
            st.write("Ticker:", ticker)
            st.write("Status:", market.get("status"))
            st.write("Title:", market.get("title"))
            st.write("Rules:", market.get("rules_primary"))
            st.write("Volume:", market.get("volume_fp"))
    for error in errors:
        st.warning(error)
    st.caption(f"Updated display: {now:%H:%M:%S} UTC • Quotes cached up to 2s • Market discovery up to 5s")
    st.info("Kalshi ask prices are not calibrated probabilities. Check fees, spread, liquidity and official settlement rules before trading.")

dashboard()
