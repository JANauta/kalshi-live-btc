
import math
from datetime import datetime, timezone
import requests
import pandas as pd
import plotly.graph_objects as go
import streamlit as st

st.set_page_config(page_title='Kalshi BTC Paper Analyzer', page_icon='₿', layout='wide')
BASE = 'https://external-api.kalshi.com/trade-api/v2'
COINBASE = 'https://api.exchange.coinbase.com/products/BTC-USD'

st.title('₿ Kalshi BTC — Manual & Paper Trading Analyzer')
st.caption('Read-only public data · No automatic orders · Coinbase is NOT the Kalshi settlement benchmark')

@st.cache_data(ttl=5, show_spinner=False)
def fetch_json(url, params=None):
    r = requests.get(url, params=params, timeout=10, headers={'User-Agent': 'KalshiBTC-PaperAnalyzer/1.0'})
    r.raise_for_status()
    return r.json()

@st.cache_data(ttl=15, show_spinner=False)
def active_market():
    markets = fetch_json(BASE + '/markets', {'series_ticker':'KXBTC15M','status':'open','limit':100}).get('markets', [])
    now = datetime.now(timezone.utc)
    candidates = []
    for m in markets:
        try:
            end = datetime.fromisoformat(m['close_time'].replace('Z','+00:00'))
            if end > now:
                candidates.append((end, m['ticker']))
        except (KeyError, TypeError, ValueError):
            pass
    return min(candidates)[1] if candidates else None

@st.cache_data(ttl=55, show_spinner=False)
def candles():
    data = fetch_json(COINBASE + '/candles', {'granularity':60})
    df = pd.DataFrame(data, columns=['time','low','high','open','close','volume'])
    if df.empty:
        return df
    df['time'] = pd.to_datetime(df['time'], unit='s', utc=True)
    return df.sort_values('time').tail(60).reset_index(drop=True)

def to_float(v):
    try:
        return float(v) if v is not None and v != '' else None
    except (ValueError, TypeError):
        return None

def fee_estimate(p, n):
    # General Kalshi taker fee formula; actual fees/rounding/series may differ.
    return math.ceil(100 * 0.07 * n * p * (1-p) - 1e-10) / 100

if 'paper' not in st.session_state:
    st.session_state.paper = []
if 'paper_initial' not in st.session_state:
    st.session_state.paper_initial = 50.0

with st.sidebar:
    st.header('Paper-trading settings')
    bankroll_start = st.number_input('Starting simulated bankroll ($)', min_value=5.0, max_value=10000.0, value=50.0, step=5.0)
    max_trade = st.number_input('Maximum simulated trade cost ($)', min_value=0.5, max_value=100.0, value=1.0, step=0.5)
    daily_stop = st.number_input('Daily loss cutoff ($)', min_value=0.5, max_value=100.0, value=3.0, step=0.5)
    st.caption('Paper entries are manual. Journal lives in this browser session; download CSV to preserve it.')
    if st.button('Reset paper journal'):
        st.session_state.paper = []
        st.session_state.paper_initial = bankroll_start
        st.rerun()

@st.fragment(run_every='5s')
def live_panel():
    st.caption('Refresh requested: ' + datetime.now(timezone.utc).strftime('%H:%M:%S UTC'))
    try:
        btc_data = fetch_json(COINBASE + '/ticker')
        price = float(btc_data['price'])
        ticker = active_market()
        if not ticker:
            st.warning('No open BTC 15-minute market found.')
            return
        m = fetch_json(BASE + '/markets/' + ticker)['market']
        target = to_float(m.get('floor_strike'))
        end = datetime.fromisoformat(m['close_time'].replace('Z','+00:00'))
        seconds = max(0, int((end - datetime.now(timezone.utc)).total_seconds()))
        yes = to_float(m.get('yes_ask_dollars'))
        no = to_float(m.get('no_ask_dollars'))
        c1,c2,c3,c4 = st.columns(4)
        c1.metric('Coinbase BTC/USD', f'${price:,.2f}')
        c2.metric('Kalshi target (verify rules)', f'${target:,.2f}' if target is not None else 'Unavailable')
        c3.metric('Time until close', f'{seconds//60:02d}:{seconds%60:02d}')
        c4.metric('Coinbase − target', f'${price-target:+,.2f}' if target is not None else 'N/A')
        st.write('**Market:**', ticker, ' | **Status:**', m.get('status'))
        left,right = st.columns(2)
        with left:
            st.subheader('UP / YES')
            st.metric('Ask', f'{yes*100:.1f}¢' if yes is not None else 'Unavailable')
            st.write('Bid:', m.get('yes_bid_dollars'))
        with right:
            st.subheader('DOWN / NO')
            st.metric('Ask', f'{no*100:.1f}¢' if no is not None else 'Unavailable')
            st.write('Bid:', m.get('no_bid_dollars'))
        df = candles()
        if not df.empty and target is not None:
            st.subheader('BTC vs. Kalshi target')
            fig = go.Figure()
            fig.add_trace(go.Scatter(x=df['time'], y=df['close'], name='Coinbase 1m close', mode='lines', line={'color':'#16a34a'}))
            fig.add_hline(y=target, line_color='#ef4444', line_dash='dash', annotation_text=f'Target ${target:,.2f}')
            fig.update_layout(height=360, xaxis_title='UTC', yaxis_title='BTC/USD', margin={'l':20,'r':20,'t':20,'b':20})
            st.plotly_chart(fig, use_container_width=True)
            returns = df['close'].pct_change().dropna()
            if len(returns) >= 10:
                st.metric('Recent 1-minute volatility (sample std.)', f'{returns.std()*100:.3f}%')
            if len(df) >= 6:
                st.metric('BTC change over last 5 closed points', f'${df["close"].iloc[-1]-df["close"].iloc[-6]:+,.2f}')
        st.caption('Market title: ' + str(m.get('title')))
        st.caption('Rules: ' + str(m.get('rules_primary')))
        st.caption('Volume: ' + str(m.get('volume_fp')))
        st.warning('Coinbase price and one-minute candles are proxies, not the Kalshi settlement benchmark. Do not infer a verified win probability from the distance alone.')
        st.session_state['latest_market'] = {'ticker':ticker, 'yes':yes, 'no':no, 'seconds':seconds, 'target':target, 'price':price}
    except Exception as e:
        st.error(f'Live feed error: {e}')

live_panel()

st.divider()
st.header('Fee-aware trade calculator (experimental)')
st.caption('Enter a hypothetical probability. This app does not claim to calculate a validated winning probability.')
market = st.session_state.get('latest_market', {})
side = st.radio('Side', ['DOWN','UP'], horizontal=True)
default_ask = market.get('no' if side == 'DOWN' else 'yes')
ask_cents = st.number_input('Entry ask (cents)', min_value=1.0, max_value=99.0, value=round(default_ask*100, 2) if default_ask is not None and 0 < default_ask < 1 else 70.0, step=1.0)
assumed_probability = st.slider('Hypothetical TRUE win probability (%)', 1, 99, 85)
n = st.number_input('Simulated contracts', min_value=1, max_value=100, value=1)
p = ask_cents / 100
fees = fee_estimate(p, n)
cost = n*p + fees
breakeven = cost/n
expected = (assumed_probability/100)*n - cost
edge_pp = 100*(assumed_probability/100 - breakeven)
c1,c2,c3,c4 = st.columns(4)
c1.metric('Estimated entry fee', f'${fees:.2f}')
c2.metric('Total cost', f'${cost:.2f}')
c3.metric('Break-even win probability', f'{breakeven*100:.2f}%')
c4.metric('Hypothetical net EV', f'${expected:+.2f}')
st.write(f'**Hypothetical fee-adjusted edge:** {edge_pp:+.2f} percentage points')
st.caption('Fee estimate uses the general 7% × contracts × price × (1−price) formula, rounded up to cents per order. Verify current series fees, order minimums and ticket totals. An 85% assumption is not a measured prediction.')

st.divider()
st.header('Manual paper-trading journal')
st.caption('Records are saved only in Streamlit session state. Download CSV frequently; Community Cloud may restart.')

entries = st.session_state.paper
resolved_pnl = sum(x['pnl'] for x in entries if x['status'] != 'OPEN')
open_cost = sum(x['cost'] for x in entries if x['status'] == 'OPEN')
current_equity = bankroll_start + resolved_pnl - open_cost
utc_today = datetime.now(timezone.utc).date().isoformat()
today_pnl = sum(x['pnl'] for x in entries if x['status'] != 'OPEN' and x['date'] == utc_today)
limit_hit = today_pnl <= -daily_stop
already = any(x['ticker'] == market.get('ticker') for x in entries)
valid_market = bool(market.get('ticker')) and market.get('seconds',0) > 0
within_window = 120 <= market.get('seconds',0) <= 240
can_record = valid_market and within_window and not already and not limit_hit and cost <= max_trade and cost <= current_equity
if not valid_market:
    st.info('Waiting for an active market before logging a simulated entry.')
elif not within_window:
    st.info('Paper-entry window: 4:00 to 2:00 remaining.')
if already:
    st.warning('A paper trade is already recorded for this market.')
if limit_hit:
    st.warning('Daily simulated loss cutoff reached.')
if cost > max_trade:
    st.warning('Selected simulated trade exceeds maximum trade cost.')
if st.button('Record simulated entry (NO REAL ORDER)', disabled=not can_record):
    st.session_state.paper.append({
        'date':utc_today, 'time_utc':datetime.now(timezone.utc).isoformat(timespec='seconds'),
        'ticker':market['ticker'], 'side':side, 'ask_cents':ask_cents,
        'contracts':int(n), 'fee':fees, 'cost':cost,
        'assumed_probability':assumed_probability, 'status':'OPEN', 'pnl':0.0
    })
    st.rerun()

st.subheader('Journal summary')
closed = [x for x in entries if x['status'] != 'OPEN']
wins = sum(x['status'] == 'WIN' for x in closed)
a,b,c,d = st.columns(4)
a.metric('Simulated equity (open cost deducted)', f'${current_equity:.2f}')
b.metric('Resolved net P&L', f'${resolved_pnl:+.2f}')
c.metric('Closed win rate', f'{100*wins/len(closed):.1f}%' if closed else 'N/A')
d.metric('Open paper trades', len(entries)-len(closed))

for idx, entry in enumerate(entries):
    if entry['status'] == 'OPEN':
        with st.expander(f"Settle {entry['ticker']} — {entry['side']} x{entry['contracts']}"):
            st.caption('Check the OFFICIAL Kalshi market settlement before marking a result. Do not infer settlement from Coinbase.')
            x,y = st.columns(2)
            if x.button('Mark WIN', key=f'win_{idx}'):
                entry['status'] = 'WIN'
                entry['pnl'] = round(entry['contracts']-entry['cost'], 2)
                st.rerun()
            if y.button('Mark LOSS', key=f'loss_{idx}'):
                entry['status'] = 'LOSS'
                entry['pnl'] = -round(entry['cost'], 2)
                st.rerun()
if entries:
    st.dataframe(pd.DataFrame(entries), use_container_width=True, hide_index=True)
    st.download_button('Download trade journal CSV', data=pd.DataFrame(entries).to_csv(index=False).encode('utf-8'), file_name='kalshi_paper_journal.csv', mime='text/csv')
else:
    st.info('No paper trades recorded yet.')

st.info('This is a manual paper-testing prototype. It does not place bets or automatically verify outcomes. Real-world fills, liquidity, market rules, fees, and benchmark differences can materially change results.')
