# --- 核心算法：計算時間加權報酬率 (TWR) ---
    # 1. 計算每一天的「單日報酬率」 (Daily Return)
    #    新公式：(End - Flow - Start) / (Start + Flow)
    
    # 確保數值型別正確 (處理空字串等問題)
    df_original["Net_Flow"] = pd.to_numeric(df_original["Net_Flow"], errors='coerce').fillna(0.0)

    df_calc = df_original.sort_values("Date").copy()
    df_calc["Prev_Assets"] = df_calc["Total_Assets"].shift(1)
    
    # 分母 = 前日資產 + 今日淨流
    denominator = df_calc["Prev_Assets"] + df_calc["Net_Flow"]
    
    # 計算報酬率 (第一筆設為 0)
    df_calc["Daily_Return"] = np.where(
        (denominator > 0) & (df_calc["Prev_Assets"].notna()),
        (df_calc["Total_Assets"] - df_calc["Net_Flow"] - df_calc["Prev_Assets"]) / denominator,
        0.0
    )
    
    # 2. 計算累積報酬指數 (Cumulative Index)
    df_calc["Cumulative_Index"] = (1 + df_calc["Daily_Return"]).cumprod()
    
    # 將計算好的指數放回主資料表以便後續篩選
    df_original["Cumulative_Index"] = df_calc["Cumulative_Index"]

    # --- 篩選與顯示 ---
    st.sidebar.markdown("---")
    st.sidebar.header("⚙️ PK 設定")
    
    time_range = st.sidebar.selectbox("區間", ["全部 (All)", "今年以來 (YTD)", "近 1 年", "近 3 個月"])
    today = pd.Timestamp.today()
    
    if time_range == "今年以來 (YTD)": start_cutoff = pd.Timestamp(today.year, 1, 1)
    elif time_range == "近 1 年": start_cutoff = today - pd.DateOffset(years=1)
    elif time_range == "近 3 個月": start_cutoff = today - pd.DateOffset(months=3)
    else: start_cutoff = df_original["Date"].min()

    # 根據時間篩選資料
    df_assets = df_original[df_original["Date"] >= start_cutoff].copy()

    if not df_assets.empty:
        BENCHMARKS = {
            "台灣加權指數 (^TWII)": "^TWII",
            "台灣 50 (0050.TW)": "0050.TW",
            "美國道瓊指數 (^DJI)": "^DJI",
            "美國標普 500 (SPY)": "SPY",
            "美國那斯達克 (QQQ)": "QQQ",
            "黃金期貨 (Gold)": "GC=F",
            "比特幣 (BTC-USD)": "BTC-USD"
        }
        
        selected_benchmarks = st.sidebar.multiselect(
            "選擇 PK 對手 (可多選)", 
            list(BENCHMARKS.keys()),
            default=["台灣加權指數 (^TWII)"]
        )

        start_date = df_assets["Date"].min().date()
        end_date = date.today() + timedelta(days=1)
        fetch_start = start_date - timedelta(days=10)

        # 準備繪圖用的 DataFrame
        comparison_df = df_assets.set_index("Date")[["Total_Assets"]].copy()
        
        # --- 正規化使用者的績效 (修正版) ---
        # 修正邏輯：基準點 (Base Index) 應該要是「區間開始前一天」的指數
        
        # 1. 嘗試尋找區間開始前的最後一筆紀錄
        mask_prev = df_original["Date"] < pd.Timestamp(start_date)
        prev_date = None
        
        if mask_prev.any():
            last_rec = df_original.loc[mask_prev].iloc[-1]
            base_index = last_rec["Cumulative_Index"]
            prev_date = last_rec["Date"]
        else:
            # 如果是歷史第一筆，則用當天的指數當基準
            base_index = df_assets["Cumulative_Index"].iloc[0]
            prev_date = None

        # 2. 計算正規化績效
        comparison_df["我的績效 (%)"] = (df_assets.set_index("Date")["Cumulative_Index"] / base_index - 1) * 100
        
        cols_to_chart = ["我的績效 (%)"]

        # 為了更精準的比較，如果 user 有 prev_date，我們的大盤抓取也要包含那一天
        if prev_date:
            fetch_start = min(fetch_start, prev_date.date() - timedelta(days=5))

        if selected_benchmarks:
            for bm_name in selected_benchmarks:
                ticker = BENCHMARKS[bm_name]
                try:
                    data = yf.download(ticker, start=fetch_start, end=end_date, progress=False)
                    if isinstance(data.columns, pd.MultiIndex):
                        data.columns = data.columns.get_level_values(0)
                    data.index = data.index.tz_localize(None)
                    
                    prices = []
                    for d in comparison_df.index:
                        relevant = data[data.index <= d]
                        if not relevant.empty:
                            val = relevant.iloc[-1]['Close']
                            if isinstance(val, pd.Series): val = val.iloc[0]
                            prices.append(float(val))
                        else:
                            prices.append(None)
                    
                    col_name = f"{bm_name} (%)"
                    temp_series = pd.Series(prices, index=comparison_df.index)
                    
                    # 修正大盤基準點：
                    # 必須找到跟 user base_index 同一個基準日 (prev_date) 的大盤價格
                    bm_base_price = None
                    
                    if prev_date:
                        # 找 prev_date 當天或之前最接近的收盤價
                        prior_data = data[data.index <= prev_date]
                        if not prior_data.empty:
                            bm_base_price = float(prior_data.iloc[-1]['Close'])
                    
                    # 如果找不到 prev_date 的價格 (或沒有 prev_date)，則退回使用區間第一筆
                    if bm_base_price is None:
                        first_valid_idx = temp_series.first_valid_index()
                        if first_valid_idx is not None:
                            bm_base_price = temp_series.loc[first_valid_idx]

                    if bm_base_price is not None:
                        comparison_df[col_name] = ((temp_series - bm_base_price) / bm_base_price) * 100
                        cols_to_chart.append(col_name)
                        
                except Exception as e:
                    st.warning(f"無法下載 {bm_name}: {e}")
