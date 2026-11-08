

import streamlit as st
import requests
import pandas as pd
import plotly.graph_objects as go

from datetime import datetime, timezone

st.set_page_config(
    page_title="Kalshi BTC Live",
    page_icon="₿",
    layout="wide"
)

BASE = "https://external-api.kalshi.com/trade-api/v2"
COINBASE = "https://api.exchange.coinbase.com/products/BTC-USD/ticker"

st.title("₿ Kalshi BTC 15-Minute Monitor")
st.caption("Live market snapshots • Manual trading only")

def get_json(url, params=None):
    r = requests.get(url, params=params, timeout=10)
    r.raise_for_status()
    return r.json()

def money(value):
    if value is None or value == "":
        return None
    return float(value)

def get_active_market():
    data = get_json(
        BASE + "/markets",
        {"series_ticker": "KXBTC15M",
         "status": "open", "limit": 100}
    )
    now = datetime.now(timezone.utc)
    candidates = []

    for market in data.get("markets", []):
        close = market.get("close_time")
        if not close:
            continue
        closing = datetime.fromisoformat(
            close.replace("Z", "+00:00")
        )
        if closing > now:
            candidates.append((closing, market))

    if not candidates:
        return None

    candidates.sort(key=lambda item: item[0])
    return candidates[0][1]["ticker"]

@st.fragment(run_every="1s")
def live_dashboard():
    now = datetime.now(timezone.utc)
    st.caption(
        "Last requested: " +
        now.strftime("%H:%M:%S UTC")
    )

    try:
        btc = get_json(COINBASE)
        st.metric(
            "Coinbase BTC/USD",
            f"${float(btc['price']):,.2f}"
        )
    except Exception as e:
        st.error(f"Coinbase feed unavailable: {e}")

    try:
        ticker = get_active_market()
        if not ticker:
            st.warning("No active BTC 15-minute market found.")
            return

        m = get_json(
            BASE + "/markets/" + ticker
        )["market"]

        st.subheader("Active Kalshi Market")
        st.write("Ticker:", ticker)
        st.write("Status:", m.get("status"))

        close = datetime.fromisoformat(
            m["close_time"].replace("Z", "+00:00")
        )
        seconds = max(
            0, int((close - now).total_seconds())
        )
        minutes, secs = divmod(seconds, 60)

        st.metric(
            "Time until market closes",
            f"{minutes:02d}:{secs:02d}"
        )

        yes_ask = money(m.get("yes_ask_dollars"))
        no_ask = money(m.get("no_ask_dollars"))
        yes_bid = money(m.get("yes_bid_dollars"))
        no_bid = money(m.get("no_bid_dollars"))

        a, b = st.columns(2)

        with a:
            st.subheader("UP / YES")
            st.metric(
                "Ask",
                f"{yes_ask:.0%}" if yes_ask is not None
                else "Unavailable"
            )
            st.write("Bid:", yes_bid)

        with b:
            st.subheader("DOWN / NO")
            st.metric(
                "Ask",
                f"{no_ask:.0%}" if no_ask is not None
                else "Unavailable"
            )
            st.write("Bid:", no_bid)

        st.write("Market title:", m.get("title"))
        st.write("Rules:", m.get("rules_primary"))
        st.write("Strike:", m.get("floor_strike"))
        
        # Kalshi target vs. Coinbase BTC chart
        target = m.get("floor_strike")

        if target is not None:
            target = float(target)

            st.subheader("BTC Price vs. Kalshi Target")

            candles = get_json(
                "https://api.exchange.coinbase.com/products/"
                "BTC-USD/candles",
                {"granularity": 60}
            )

            df = pd.DataFrame(
                candles,
                columns=[
                    "time", "low", "high",
                    "open", "close", "volume"
                ]
            )

            df["time"] = pd.to_datetime(
                df["time"], unit="s", utc=True
            )
            df = df.sort_values("time").tail(30)

            fig = go.Figure()

            fig.add_trace(go.Scatter(
                x=df["time"],
                y=df["close"],
                mode="lines",
                name="Coinbase BTC",
                line=dict(color="#16a34a", width=2)
            ))

            fig.add_hline(
                y=target,
                line_color="#ef4444",
                line_dash="dash",
                annotation_text=f"Kalshi Target ${target:,.2f}"
            )

            fig.update_layout(
                height=420,
                xaxis_title="Time (UTC)",
                yaxis_title="BTC/USD",
                hovermode="x unified"
            )

            st.plotly_chart(
                fig,
                use_container_width=True
            )

            current_btc = float(btc["price"])
            difference = current_btc - target

            st.metric(
                "Coinbase Distance From Kalshi Target",
                f"${difference:+,.2f}"
            )

            if difference > 0:
                st.success("Coinbase BTC is ABOVE target")
            elif difference < 0:
                st.error("Coinbase BTC is BELOW target")
            else:
                st.warning("Coinbase BTC is AT target")
        else:
            st.warning(
                "Target price unavailable from Kalshi API."
            )

        st.write("Volume:", m.get("volume_fp"))

        st.info(
            "Ask prices are not verified probabilities. "
            "Check market rules, fees, spreads and "
            "settlement reference before trading."
        )

    except Exception as e:
        st.error(f"Kalshi feed unavailable: {e}")

live_dashboard()
