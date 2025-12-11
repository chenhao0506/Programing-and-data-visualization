import os
import ee
import geemap
import json
import dash
from dash import dcc, html
import plotly.express as px
import pandas as pd
from google.oauth2 import service_account

# ----------------------------------------------------
# 1. Earth Engine 初始化 (Hugging Face Secret 讀取)
# ----------------------------------------------------
print("--- 程式啟動與初始化 ---")
# 驗證 geemap 是否匯入成功
print(f"geemap version: {geemap.__version__}")

GEE_SERVICE_SECRET = os.environ.get("GEE_SERVICE_SECRET", "")
if not GEE_SERVICE_SECRET:
    raise ValueError(
        "請先在 Hugging Face 設定環境變數 GEE_SERVICE_SECRET，內容為完整的 JSON 字串"
    )

# 嘗試將 JSON 字串轉成 Python 字典
try:
    service_account_info = json.loads(GEE_SERVICE_SECRET)
except json.JSONDecodeError as e:
    raise ValueError(f"JSON 格式錯誤，請檢查 Secret 內容是否為有效的 JSON 字串: {e}")

# 建立 GEE Credentials
credentials = service_account.Credentials.from_service_account_info(
    service_account_info,
    scopes=["https://www.googleapis.com/auth/earthengine"]
)

# 初始化 Earth Engine
ee.Initialize(credentials)
print("Earth Engine 初始化成功")

# ----------------------------------------------------
# 2. GEE 數據獲取與處理
# ----------------------------------------------------

# 測試 geemap Map 建立 (主要用於確認套件功能正常，在地圖應用中才需要)
m = geemap.Map(center=(23.97, 120.53), zoom=10)
print(f"Map 建立成功: {type(m)}")

# 定義研究範圍與年份
region = ee.Geometry.Rectangle([120.48, 23.90, 120.65, 24.10])
years = list(range(2013, 2024))

# 函數：取得七月含雲量最低的 Landsat 8 B10 紅外波段平均值
# 函數：取得七月含雲量最低的 Landsat 8 ST_B10 (Collection 2) 平均值
def get_l8_july(year):
    collection = (
        # *** 修正點 1: 使用 C02/T1_L2 路徑 ***
        ee.ImageCollection("LANDSAT/LC08/C02/T1_L2") 
        .filterBounds(region)
        .filterDate(f"{year}-07-01", f"{year}-07-31")
        # *** 修正點 2: 使用 ST_B10 波段名稱 ***
        .select("ST_B10") 
        .sort("CLOUD_COVER")
    )
    
    first_image = collection.first()
    if first_image is None:
        print(f"Warning: No Landsat 8 image found for July {year}.")
        return None
        
    image = ee.Image(first_image).clip(region)
    stats = image.reduceRegion(
        reducer=ee.Reducer.mean(),
        geometry=region,
        scale=30,
        bestEffort=True
    )
    # 這裡的鍵名也要從 'B10' 改為 'ST_B10'
    value = stats.get('ST_B10').getInfo() if stats.get('ST_B10') else None
    return value

# 預計算每年的紅外值
print("--- 數據獲取中 (請耐心等候 GEE 計算) ---")
data = []
for year in years:
    val = get_l8_july(year)
    data.append({"year": year, "value": val})

df = pd.DataFrame(data).dropna().astype({'year': 'int'}) # 排除 None 值並確保年份為整數
print("--- 數據獲取完成 ---")
print(df)


# ----------------------------------------------------
# 3. Dash App 建立
# ----------------------------------------------------
app = dash.Dash(__name__)

# 初始圖表 (顯示所有數據)
if not df.empty:
    min_val = df['value'].min() * 0.95
    max_val = df['value'].max() * 1.05
    initial_fig = px.bar(
        df,
        x="year",
        y="value",
        text="value",
        title="Landsat 8 七月 B10 紅外波段平均值 (2013-2023)",
        labels={"year": "年份", "value": "B10 紅外值"},
        # 設置所有柱狀圖的預設顏色
        color_discrete_sequence=['#1f77b4'] 
    )
    initial_fig.update_traces(texttemplate='%{text:.2f}', textposition='outside')
    initial_fig.update_yaxes(range=[min_val, max_val])
else:
    # 處理 DataFrame 為空的情況
    initial_fig = {}
    
app.layout = html.Div([
    html.H1("Landsat 8 七月紅外波段瀏覽 - GEE/Dash 應用", style={'textAlign': 'center'}),
    
    html.Div(id='data-status', children='數據已載入。' if not df.empty else '錯誤：未成功獲取 Landsat 數據。', 
             style={'textAlign': 'center', 'color': 'green' if not df.empty else 'red'}),
             
    dcc.Graph(id='graph', figure=initial_fig),
    
    html.Div([
        html.H3("選擇年份以高亮顯示:", style={'padding-right': '30px'}),
        dcc.Slider(
            id='year-slider',
            min=min(years),
            max=max(years),
            step=1,
            value=max(years) if not df.empty else min(years),
            marks={str(y): {'label': str(y), 'style': {'color': '#77b0b1'}} for y in years},
            tooltip={"placement": "bottom", "always_visible": True},
            disabled=df.empty
        ),
    ], style={'padding': '20px', 'width': '80%', 'margin': '0 auto'})
])

# ----------------------------------------------------
# 4. Callback：更新圖表 (高亮選定的年份)
# ----------------------------------------------------
@app.callback(
    dash.Output('graph', 'figure'),
    [dash.Input('year-slider', 'value')]
)
def update_graph(selected_year):
    if df.empty:
        return {}
        
    # 重新創建基礎圖表
    fig = px.bar(
        df,
        x="year",
        y="value",
        text="value",
        title="Landsat 8 七月 B10 紅外波段平均值 (2013-2023)",
        labels={"year": "年份", "value": "B10 紅外值"}
    )
    
    # 高亮邏輯
    colors = ['#1f77b4'] * len(df) # 預設顏色 (藍色)
    
    # 找到選中年份的索引
    try:
        selected_index = df[df['year'] == selected_year].index[0]
        colors[selected_index] = '#ff7f0e' # 高亮顏色 (橘色)
    except IndexError:
        # 如果滑桿值在數據框中找不到，則不做任何高亮
        pass 

    # 更新柱狀圖顏色
    min_val = df['value'].min() * 0.95
    max_val = df['value'].max() * 1.05

    fig.update_traces(
        marker_color=colors,
        texttemplate='%{text:.2f}',
        textposition='outside'
    )
    
    # 確保 Y 軸範圍固定
    fig.update_yaxes(range=[min_val, max_val])
    
    return fig

# ----------------------------------------------------
# 5. Dash App 啟動 (Hugging Face Port 環境變數處理)
# ----------------------------------------------------
if __name__ == '__main__':
    app.run(host="0.0.0.0", port=7860, debug=False)