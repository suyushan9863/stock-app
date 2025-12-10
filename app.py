import streamlit as st
import pandas as pd
import yfinance as yf
import gspread
from oauth2client.service_account import ServiceAccountCredentials
from datetime import date, timedelta
import os
import json

# --- 設定頁面資訊 ---
st.set_page_config(page_title="私人資產儀表板", layout="wide")

# --- 0. 🔐 安全認證 (密碼鎖) ---
def check_password():
    """回傳 True 如果使用者輸入正確密碼"""
    
    # 從 secrets 讀取密碼，如果沒設定則預設為 "1234"
    if "app_password" in st.secrets:
        CORRECT_PASSWORD = st.secrets["app_password"]
    else:
        CORRECT_PASSWORD = "1234" 

    if "password_correct" not in st.session_state:
        st.session_state.password_correct = False

    if st.session_state.password_correct:
        return True

    # 顯示密碼輸入框
    st.title("🔒 請輸入密碼以存取資料")
    password = st.text_input("Password", type="password")
    
    if st.button("登入"):
        if password == CORRECT_PASSWORD:
            st.session_state.password_correct = True
            st.rerun()
        else:
            st.error("密碼錯誤")
    return False

# 如果密碼沒過，就停止執行後面程式
if not check_password():
    st.stop() 

# --- 通過驗證後才會執行以下內容 ---
st.title("☁️ 雲端版：投資績效 PK 擂台")

# --- 設定 ---
KEY_FILE = "secrets.json"
GOOGLE_SHEET_NAME = "My_Stock_Portfolio" 

# --- 1. 雲端資料庫連線 (支援 Streamlit Cloud) ---
@st.cache_resource
def get_google_sheet_client():
    scope = ['https://spreadsheets.google.com/feeds', 'https://www.googleapis.com/auth/drive']
    
    # 策略 A: 優先嘗試從 Streamlit Cloud 的 Secrets 讀取 (雲端部署用)
    if "gcp_service_account" in st.secrets:
        try:
            # 必須將 st.secrets 轉換為標準 dict 格式
            creds_dict = dict(st.secrets["gcp_service_account"])
            
            # 🚨【關鍵修復】🚨
            # 強制處理 secrets 裡的換行符號問題
            if "private_key" in creds_dict:
                creds_dict["private_key"] = creds_dict["private_key"].replace("\\n", "\n")

            creds = ServiceAccountCredentials.from_json_keyfile_dict(creds_dict, scope)
            client = gspread.authorize(creds)
            return client
        except Exception as e:
            st.error(f"雲端 Secrets 認證失敗: {e}")
            return None

    # 策略 B: 如果雲端沒有，則嘗試讀取本機檔案 (本機開發用)
    try:
        creds = ServiceAccountCredentials.from_json_keyfile_name(KEY_FILE, scope)
        client = gspread.authorize(creds)
        return client
    except FileNotFoundError:
        st.error("❌ 找不到金鑰！(本機找不到 secrets.json，雲端也沒有設定 secrets)")
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
        if not data: return pd.DataFrame(columns=["Date", "Total_Assets", "Note"])
        df = pd.DataFrame(data)
        if "Date" not in df.columns: df = pd.DataFrame(columns=["Date", "Total_Assets", "Note"])
        return df
    except Exception as e:
        st.error(f"讀取資料失敗: {e}")
        return None

def save_data(date_input, asset_value, note):
    df = load_data()
    if df is None: return None
    
    new_data = pd.DataFrame({
        "Date": [str(date_input)],
        "Total_Assets": [asset_value],
        "Note": [note]
    })
    
    df = pd.concat([df, new_data], ignore_index=True)
    df["Date"] = pd.to_datetime(df["Date"])
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
input_assets = st.sidebar.number_input("總資產 (TWD)", min_value=0, step=10000)
input_note = st.sidebar.text_input("備註")

if st.sidebar.button("💾 儲存"):
    with st.spinner("同步中..."):
        save_data(input_date, input_assets, input_note)
        st.success("已更新！")
        st.rerun()

# --- 主畫面顯示 ---
if df_original is not None and not df_original.empty:
    df_original["Date"] = pd.to_datetime(df_original["Date"])
    
    st.sidebar.markdown("---")
    st.sidebar.header("⚙️ PK 設定")
    
    # 時間篩選
    time_range = st.sidebar.selectbox("區間", ["全部 (All)", "今年以來 (YTD)", "近 1 年", "近 3 個月"])
    today = pd.Timestamp.today()
    if time_range == "今年以來 (YTD)": start_cutoff = pd.Timestamp(today.year, 1, 1)
    elif time_range == "近 1 年": start_cutoff = today - pd.DateOffset(years=1)
    elif time_range == "近 3 個月": start_cutoff = today - pd.DateOffset(months=3)
    else: start_cutoff = df_original["Date"].min()

    df_assets = df_original[df_original["Date"] >= start_cutoff].copy()

    if not df_assets.empty:
        # --- 多重 Benchmark 設定 ---
        BENCHMARKS = {
            "台灣加權指數 (^TWII)": "^TWII",
            "台灣 50 (0050.TW)": "0050.TW",
            "美國道瓊指數 (^DJI)": "^DJI",     # 新增
            "美國標普 500 (SPY)": "SPY",
            "美國那斯達克 (QQQ)": "QQQ",
            "黃金期貨 (Gold)": "GC=F",        # 新增
            "比特幣 (BTC-USD)": "BTC-USD"
        }
        
        # 改用 multiselect 支援多選
        selected_benchmarks = st.sidebar.multiselect(
            "選擇 PK 對手 (可多選)", 
            list(BENCHMARKS.keys()),
            default=["台灣加權指數 (^TWII)"]
        )

        # 準備資料
        start_date = df_assets["Date"].min().date()
        end_date = date.today() + timedelta(days=1)
        fetch_start = start_date - timedelta(days=10) # 多抓一點緩衝

        comparison_df = df_assets.set_index("Date")[["Total_Assets"]].copy()
        initial_asset = comparison_df["Total_Assets"].iloc[0]
        comparison_df["我的績效 (%)"] = ((comparison_df["Total_Assets"] - initial_asset) / initial_asset) * 100
        
        # 繪圖欄位列表
        cols_to_chart = ["我的績效 (%)"]

        # 迴圈抓取每個被選中的 Benchmark
        if selected_benchmarks:
            for bm_name in selected_benchmarks:
                ticker = BENCHMARKS[bm_name]
                try:
                    # 下載資料
                    data = yf.download(ticker, start=fetch_start, end=end_date, progress=False)
                    
                    # 資料清理 (Flatten)
                    if isinstance(data.columns, pd.MultiIndex):
                        data.columns = data.columns.get_level_values(0)
                    data.index = data.index.tz_localize(None)
                    
                    # 對齊使用者的日期
                    prices = []
                    for d in comparison_df.index:
                        # 找該日期前最新的收盤價
                        relevant = data[data.index <= d]
                        if not relevant.empty:
                            val = relevant.iloc[-1]['Close']
                            # 處理 Series 格式
                            if isinstance(val, pd.Series): val = val.iloc[0]
                            prices.append(float(val))
                        else:
                            prices.append(None)
                    
                    # 計算績效
                    col_name = f"{bm_name} (%)"
                    temp_series = pd.Series(prices, index=comparison_df.index)
                    
                    # 找到第一個非空值作為基準點
                    first_valid_idx = temp_series.first_valid_index()
                    if first_valid_idx is not None:
                        base_price = temp_series.loc[first_valid_idx]
                        comparison_df[col_name] = ((temp_series - base_price) / base_price) * 100
                        cols_to_chart.append(col_name)
                        
                except Exception as e:
                    st.warning(f"無法下載 {bm_name}: {e}")

        # 繪圖
        st.line_chart(comparison_df[cols_to_chart])
        
        with st.expander("詳細數據"):
            st.dataframe(comparison_df)
    else:
        st.warning("區間內無資料")
else:
    st.info("請輸入第一筆資產")
