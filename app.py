import streamlit as st
import pandas as pd
import yfinance as yf
import gspread
from oauth2client.service_account import ServiceAccountCredentials
from datetime import date, timedelta
import os
import json
import numpy as np

# --- 設定頁面資訊 ---
st.set_page_config(page_title="私人資產儀表板", layout="wide")

# --- 0. 🔐 安全認證 (密碼鎖) ---
def check_password():
    """回傳 True 如果使用者輸入正確密碼"""
    if "app_password" in st.secrets:
        CORRECT_PASSWORD = st.secrets["app_password"]
    else:
        CORRECT_PASSWORD = "1234" 

    if "password_correct" not in st.session_state:
        st.session_state.password_correct = False

    if st.session_state.password_correct:
        return True

    st.title("🔒 請輸入密碼以存取資料")
    password = st.text_input("Password", type="password")
    
    if st.button("登入"):
        if password == CORRECT_PASSWORD:
            st.session_state.password_correct = True
            st.rerun()
        else:
            st.error("密碼錯誤")
    return False

if not check_password():
    st.stop() 

# --- 通過驗證後才會執行以下內容 ---
st.title("☁️ 雲端版：投資績效 PK 擂台 (TWR 修正版)")

# --- 設定 ---
KEY_FILE = "secrets.json"
GOOGLE_SHEET_NAME = "My_Stock_Portfolio" 

# --- 1. 雲端資料庫連線 ---
@st.cache_resource
def get_google_sheet_client():
    scope = ['https://spreadsheets.google.com/feeds', 'https://www.googleapis.com/auth/drive']
    
    if "gcp_service_account" in st.secrets:
        try:
            creds_dict = dict(st.secrets["gcp_service_account"])
            if "private_key" in creds_dict:
                creds_dict["private_key"] = creds_dict["private_key"].replace("\\n", "\n")
            creds = ServiceAccountCredentials.from_json_keyfile_dict(creds_dict, scope)
            client = gspread.authorize(creds)
            return client
        except Exception as e:
            st.error(f"雲端 Secrets 認證失敗: {e}")
            return None

    try:
        creds = ServiceAccountCredentials.from_json_keyfile_name(KEY_FILE, scope)
        client = gspread.authorize(creds)
        return client
    except FileNotFoundError:
        st.error("❌ 找不到金鑰！")
        return None
    except Exception as e:
        st.error(f"認證失敗: {e}")
        return None

def load_data():
    client = get_google_sheet_client()
    if client is None: return None
        
    try:
        sheet = client.open(GOOGLE_SHEET_NAME).sheet1
        data = sheet.get_all_records()
        if not data: return pd.DataFrame(columns=["Date", "Total_Assets", "Net_Flow", "Note"])
        
        df = pd.DataFrame(data)
        
        # 欄位防呆
        if "Date" not in df.columns: 
            df = pd.DataFrame(columns=["Date", "Total_Assets", "Net_Flow", "Note"])
        if "Net_Flow" not in df.columns:
            df["Net_Flow"] = 0.0
            
        return df
    except Exception as e:
        st.error(f"讀取資料失敗: {e}")
        return None

def save_data(date_input, asset_value, net_flow, note):
    df = load_data()
    if df is None: return None
    
    new_data = pd.DataFrame({
        "Date": [str(date_input)],
        "Total_Assets": [float(asset_value)],
        "Net_Flow": [float(net_flow)],
        "Note": [str(note)]
    })
    
    df = pd.concat([df, new_data], ignore_index=True)
    df["Date"] = pd.to_datetime(df["Date"])
    df["Net_Flow"] = df["Net_Flow"].fillna(0.0)
    df["Total_Assets"] = df["Total_Assets"].astype(float)
    
    df = df.drop_duplicates(subset=["Date"], keep="last")
    df = df.sort_values(by="Date")
    
    try:
        client = get_google_sheet_client()
        sheet = client.open(GOOGLE_SHEET_NAME).sheet1
        df_export = df.copy()
        df_export["Date"] = df_export["Date"].dt.strftime('%Y-%m-%d')
        sheet.clear()
        data_to_write = [df_export.columns.values.tolist()] + df_export.values.tolist()
        sheet.update(data_to_write)
        return df
    except Exception as e:
        st.error(f"寫入失敗: {e}")
        return df

# --- 側邊欄輸入區 ---
df_original = load_data()

st.sidebar.header("📝 紀錄資產")
input_date = st.sidebar.date_input("日期", date.today())
input_assets = st.sidebar.number_input("總資產 (含今日入金)", min_value=0.0, step=10000.0, format="%.0f")

st.sidebar.markdown("---")
input_flow = st.sidebar.number_input(
    "💰 資金異動 (選填)", 
    value=0.0, 
    step=10000.0, 
    help="入金請填正數，出金請填負數。注意：上方的「總資產」必須包含這筆入金金額！"
)

# --- 🚨 關鍵防呆：預判損益邏輯 ---
if df_original is not None and not df_original.empty:
    # 確保數值正確
    if "Net_Flow" not in df_original.columns: df_original["Net_Flow"] = 0.0
    df_original["Total_Assets"] = pd.to_numeric(df_original["Total_Assets"], errors='coerce').fillna(0.0)
    
    last_record = df_original.sort_values("Date").iloc[-1]
    last_assets = float(last_record["Total_Assets"])
    
    # 預估今日投資損益 = (今日總資產 - 資金異動 - 昨日總資產)
    est_profit = input_assets - input_flow - last_assets
    
    # 簡單分母 (避免除以0)
    denom = last_assets + (input_flow if input_flow > 0 else 0)
    est_roi = (est_profit / denom * 100) if denom > 0 else 0.0

    st.sidebar.info(f"""
    📊 **試算檢查：**
    昨日資產：{last_assets:,.0f}
    今日資產：{input_assets:,.0f} (含異動)
    扣除異動後：{input_assets - input_flow:,.0f}
    -----------------------
    推算今日投資損益：**{est_profit:+,.0f}** ({est_roi:+.2f}%)
    """)

    # 如果入金後，推算出的損益是大賠，高機率是忘了把入金加到總資產
    if input_flow > 0 and est_roi < -10:
        st.sidebar.error("⚠️ **警告：損益異常暴跌！**\n你填寫了入金，但總資產似乎沒有增加？\n\n👉 請確認「總資產」欄位已經**加上**了這筆入金金額。")

else:
    st.sidebar.caption("輸入第一筆資料後即可看到試算結果。")

st.sidebar.markdown("---")
input_note = st.sidebar.text_input("備註")

if st.sidebar.button("💾 儲存"):
    with st.spinner("同步中..."):
        save_data(input_date, input_assets, input_flow, input_note)
        st.success("已更新！")
        st.rerun()

# --- 主畫面顯示 ---
if df_original is not None and not df_original.empty:
    df_original["Date"] = pd.to_datetime(df_original["Date"])
    df_original["Total_Assets"] = pd.to_numeric(df_original["Total_Assets"], errors='coerce').fillna(0.0)
    df_original["Net_Flow"] = pd.to_numeric(df_original["Net_Flow"], errors='coerce').fillna(0.0)

    # --- 核心算法：Simple Dietz Method ---
    # 改用 Simple Dietz (權重 0.5) 讓曲線更平滑合理
    
    df_calc = df_original.sort_values("Date").copy()
    df_calc["Prev_Assets"] = df_calc["Total_Assets"].shift(1)
    
    # 公式：報酬率 = (期末 - 期初 - 淨流) / (期初 + 0.5 * 淨流)
    # 假設資金在盤中進出，權重設為 0.5 (如果分母 <= 0 則設為 0)
    denominator = df_calc["Prev_Assets"] + (df_calc["Net_Flow"] * 0.5)
    
    df_calc["Daily_Return"] = np.where(
        (denominator > 0) & (df_calc["Prev_Assets"].notna()),
        (df_calc["Total_Assets"] - df_calc["Net_Flow"] - df_calc["Prev_Assets"]) / denominator,
        0.0
    )
    
    # 累積報酬指數
    df_calc["Cumulative_Index"] = (1 + df_calc["Daily_Return"]).cumprod()
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

    # 包含「錨點」的篩選邏輯
    df_sorted = df_original.sort_values("Date").reset_index(drop=True)
    mask_after = df_sorted["Date"] >= start_cutoff
    
    if mask_after.any():
        first_idx = mask_after.idxmax()
        start_idx = max(0, first_idx - 1)
        df_assets = df_sorted.iloc[start_idx:].copy()
    else:
        df_assets = pd.DataFrame()

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

        chart_start_date = df_assets["Date"].min().date()
        chart_end_date = date.today() + timedelta(days=1)
        fetch_start = chart_start_date - timedelta(days=10)

        comparison_df = df_assets.set_index("Date")[["Total_Assets"]].copy()
        
        base_index = df_assets["Cumulative_Index"].iloc[0]
        comparison_df["我的績效 (%)"] = (df_assets.set_index("Date")["Cumulative_Index"] / base_index - 1) * 100
        
        cols_to_chart = ["我的績效 (%)"]

        if selected_benchmarks:
            for bm_name in selected_benchmarks:
                ticker = BENCHMARKS[bm_name]
                try:
                    data = yf.download(ticker, start=fetch_start, end=chart_end_date, progress=False)
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
                    
                    first_valid_idx = temp_series.first_valid_index()
                    if first_valid_idx is not None:
                        base_price = temp_series.loc[first_valid_idx]
                        comparison_df[col_name] = ((temp_series - base_price) / base_price) * 100
                        cols_to_chart.append(col_name)
                        
                except Exception as e:
                    st.warning(f"無法下載 {bm_name}: {e}")

        st.line_chart(comparison_df[cols_to_chart])
        
        latest_return = comparison_df["我的績效 (%)"].iloc[-1]
        st.metric("區間報酬率", f"{latest_return:.2f}%")
        
        with st.expander("詳細數據 (含 Net Flow)"):
            st.dataframe(df_assets.sort_values("Date", ascending=False))
            st.caption("Net_Flow: 正數代表入金，負數代表出金")
    else:
        st.warning("區間內無資料")
else:
    st.info("請輸入第一筆資產")
