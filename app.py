st.caption("Read-only market data · Experimental probability model · No automatic orders")
with st.sidebar:
    st.header("Model & risk settings")
    count = st.number_input("Contracts for illustration", min_value=1, max_value=1000, value=5)
    min_edge = st.slider("Minimum model edge", 0.01, 0.30, 0.10, 0.01, format="%.0f%%")
    buffer = st.number_input("Reference-price uncertainty buffer ($)", min_value=0.0, max_value=500.0, value=25.0, step=5.0)
    min_seconds = st.number_input("Do not signal with less than (seconds)", min_value=0, max_value=600, value=60, step=15)
    min_sigma = st.number_input("Minimum 1-minute volatility (decimal)", min_value=0.000001, max_value=0.01, value=0.00001, format="%.6f")
    st.caption("Edge = estimated win probability − fee-adjusted break-even probability. The uncertainty buffer evaluates adverse price shifts; it does not correct a biased reference feed.")


@st.fragment(run_every="5s")
def dashboard():
    now = datetime.now(timezone.utc)
    st.caption("Refresh requested: " + now.strftime("%Y-%m-%d %H:%M:%S UTC") + " · Quotes cached up to 5s; candles up to 60s")
    try:
        ticker = active_market()
        if not ticker:
            st.warning("No open BTC 15-minute market was found. No signal.")
            return
        m = market_details(ticker)
        bt = btc_ticker()
        df = candles()
        price = as_float(bt.get("price"))
        strike = as_float(m.get("floor_strike"))
        up_ask = as_float(m.get("yes_ask_dollars"))
        down_ask = as_float(m.get("no_ask_dollars"))
        close = datetime.fromisoformat(m["close_time"].replace("Z", "+00:00"))
        seconds = max(0, int((close - now).total_seconds()))
        ticker_ts = datetime.fromisoformat(bt["time"].replace("Z", "+00:00")) if bt.get("time") else None
        quote_age = (now - ticker_ts).total_seconds() if ticker_ts else None
        st.write("**Market:**", ticker)
        st.caption("Market: " + str(m.get("title", "")))
        a, b, c, d = st.columns(4)
        a.metric("Coinbase BTC", f"${price:,.2f}" if price is not None else "N/A")
        b.metric("Kalshi target", f"${strike:,.2f}" if strike is not None else "N/A")
        c.metric("Distance (Coinbase)", f"${price-strike:+,.2f}" if price is not None and strike is not None else "N/A")
        d.metric("Time remaining", f"{seconds//60:02d}:{seconds%60:02d}")
        a, b = st.columns(2)
        a.metric("UP ask", f"{up_ask:.0%}" if up_ask is not None else "N/A")
        b.metric("DOWN ask", f"{down_ask:.0%}" if down_ask is not None else "N/A")

        if not df.empty and strike is not None:
            plot = df.tail(30).copy()
            plot["datetime"] = pd.to_datetime(plot["time"], unit="s", utc=True)
            fig = go.Figure()
            fig.add_trace(go.Scatter(x=plot["datetime"], y=plot["close"], name="Coinbase 1m close", mode="lines"))
            fig.add_hline(y=strike, line_dash="dash", annotation_text=f"Kalshi target ${strike:,.2f}")
            fig.update_layout(height=350, xaxis_title="UTC", yaxis_title="BTC/USD")
            st.plotly_chart(fig, use_container_width=True)

        st.subheader("Model estimates (experimental)")
        reasons = []
        if m.get("status") not in ("active", "open"):
            reasons.append("Market is not confirmed active/open")
        if price is None or strike is None or up_ask is None or down_ask is None:
            reasons.append("Required price, target, or ask missing")
        if up_ask is not None and not (0 < up_ask < 1):
            reasons.append("UP ask invalid or untradeable")
        if down_ask is not None and not (0 < down_ask < 1):
            reasons.append("DOWN ask invalid or untradeable")
        if quote_age is None or quote_age < -5 or quote_age > 15:
            reasons.append("Coinbase quote timestamp missing or stale")
        if seconds < min_seconds:
            reasons.append("Too close to market close")
        if len(df) < 31:
            reasons.append("Insufficient historical candles")
        if df.empty or now.timestamp() - float(df["time"].max()) > 180:
            reasons.append("Candle history is stale")

        prob_up = sigma = None
        robust_up = robust_down = None
        if price is not None and strike is not None and len(df) >= 31:
            prob_up, sigma = estimate_probability(price, strike, seconds, df, min_sigma)
            if prob_up is not None:
                # Worst-case side probabilities across an assumed reference-price offset.
                robust_up, _ = estimate_probability(max(0.01, price - buffer), strike, seconds, df, min_sigma)
                upper_up, _ = estimate_probability(price + buffer, strike, seconds, df, min_sigma)
                robust_down = 1 - upper_up if upper_up is not None else None
            else:
                reasons.append("Volatility estimate unavailable or too low")

        try:
            series = series_info()
            fee_type = str(series.get("fee_type", ""))
            multiplier = as_float(series.get("fee_multiplier"))
            if fee_type not in ("quadratic", "quadratic_with_maker_fees", "quadratic_with_combo_maker_fees") or multiplier is None or multiplier < 0:
                reasons.append("Fee schedule could not be verified from series metadata")
        except Exception:
            multiplier = None
            reasons.append("Fee schedule unavailable")

        rows = []
        for side, ask, probability, conservative in [
            ("UP", up_ask, prob_up, robust_up),
            ("DOWN", down_ask, None if prob_up is None else 1 - prob_up, robust_down),
        ]:
            if ask is None or not (0 < ask < 1) or multiplier is None:
                continue
            fee = fee_estimate(ask, int(count), multiplier)
            all_in = ask * int(count) + fee
            breakeven = all_in / int(count)
            ev = None if probability is None else probability * int(count) - all_in
            conservative_edge = None if conservative is None else conservative - breakeven
            rows.append({"Side": side, "Ask": f"{ask:.1%}", "Model win %": f"{probability:.1%}" if probability is not None else "N/A", "Break-even %": f"{breakeven:.1%}", "Est. entry fee": f"${fee:.2f}", "EV (hold to settlement)": f"${ev:+.2f}" if ev is not None else "N/A", "Conservative edge": f"{conservative_edge:+.1%}" if conservative_edge is not None else "N/A", "_edge": conservative_edge, "_side": side})
        if rows:
            st.dataframe(pd.DataFrame([{k: v for k, v in row.items() if not k.startswith("_")} for row in rows]), hide_index=True, use_container_width=True)
        if sigma is not None:
            st.caption(f"Observed 1-minute log-return volatility: {sigma:.5%}. Model assumes zero predictable drift and constant volatility; it is not calibrated to Kalshi outcomes.")
        st.caption(f"Coinbase quote age: {quote_age:.1f}s" if quote_age is not None else "Coinbase quote age: unknown")
        candidates = [r for r in rows if r["_edge"] is not None and r["_edge"] >= min_edge]
        if reasons:
            st.warning("SKIP — " + "; ".join(reasons))
        elif candidates:
            best = max(candidates, key=lambda r: r["_edge"])
            st.info(f"MODEL WATCH: BUY {best['_side']} candidate (manual only). Conservative estimated edge: {best['_edge']:+.1%}. Verify the Kalshi settlement index, order preview, liquidity and live price before acting.")
        else:
            st.info("SKIP — Neither side exceeds your conservative minimum edge.")
        st.caption("EV assumes buying at displayed ask, holding to settlement, $1 payout if correct, and the estimated taker fee. Actual fill, index differences, slippage, fees and model error may change results. No trades are placed.")
        with st.expander("Contract rules and feed limitations"):
            st.write(m.get("rules_primary", "Rules unavailable"))
            st.write("Coinbase BTC/USD is NOT Kalshi's official settlement reference. Kalshi BTC 15-minute markets use a specified benchmark and averaging window; this approximation cannot establish a reliable edge without backtesting and calibration.")
    except requests.HTTPError as exc:
        st.error(f"Data request failed: {exc}. Try again after API rate limits clear.")
    except Exception as exc:
        st.error(f"Dashboard error: {exc}")


