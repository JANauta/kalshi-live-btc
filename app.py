
import streamlit as st
import requests
from datetime import datetime, timezone

st.set_page_config(
    page_title="Kalshi BTC Dashboard",
    page_icon="₿",
    layout="wide"
)

st.title("₿ Kalshi BTC 15-Minute Dashboard")
st.caption("Read-only market monitor — no automatic trades")

@st.cache_data(ttl=5)
def get_btc_price():
    url = "https://api.exchange.coinbase.com/products/BTC-USD/ticker"
    response = requests.get(url, timeout=10)
    response.raise_for_status()
    return float(response.json()["price"])

st.subheader("Bitcoin Live Price")

try:
    price = get_btc_price()
    st.metric("BTC / USD", f"${price:,.2f}")
    st.success("Coinbase price retrieved successfully")
except Exception as error:
    st.error(f"Unable to retrieve BTC price: {error}")

st.subheader("Kalshi 15-Minute Market")
st.info("Kalshi contract data will be added in the next step.")

st.caption(
    "Coinbase prices may differ from the reference "
    "used to settle Kalshi contracts."
)

st.write(
    "Last page run:",
    datetime.now(timezone.utc).strftime("%H:%M:%S UTC")
)

if st.button("Refresh market data"):
    st.cache_data.clear()
    st.rerun()
