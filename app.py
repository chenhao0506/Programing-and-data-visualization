import os
import json
import ee
import dash
import dash_leaflet as dl
import dash_bootstrap_components as dbc
from dash import dcc, html, Output, Input, State
from google.oauth2 import service_account

# ----------------------------------------------------
# 1. Earth Engine 初始化 (請確保環境變數已設定)
# ----------------------------------------------------
GEE_SECRET = os.environ.get("GEE_SERVICE_SECRET", "")
if not GEE_SECRET:
    try:
        ee.Initialize()
    except Exception as e:
        print(f"Earth Engine 初始化失敗: {e}")
else:
    service_account_info = json.loads(GEE_SECRET)
    credentials = service_account.Credentials.from_service_account_info(
        service_account_info,
        scopes=["https://www.googleapis.com/auth/earthengine"]
    )
    ee.Initialize(credentials)

# ----------------------------------------------------
# 2. GEE 數據處理函數 (LST & NDVI)
# ----------------------------------------------------
# 研究區域
taiwan_region = ee.Geometry.Rectangle([120.24, 23.77, 120.69, 24.20])
years = list(range(2015, 2026))

# LST 視覺化參數 (保持對比度)
LST_VIS = {
    'min': 20, 
    'max': 45, 
    'palette': ['040274', '0402ff', '3be285', 'ffd611', 'ff0000', '911003']
}

NDVI_VIS = {
    'min': 0.0, 'max': 0.8, 
    'palette': ['FFFFFF', 'CE7E45', 'F1B555', '66A000', '207401', '056201', '004C00']
}

def mask_clouds_and_scale(image):
    """ 基礎去雲 """
    qa = image.select('QA_PIXEL')
    # Bit 3: Cloud, Bit 4: Cloud Shadow
    mask = qa.bitwiseAnd(1 << 3).eq(0).And(qa.bitwiseAnd(1 << 4).eq(0))
    # 這裡做基本的擴張就好，剩下的交給溫差過濾
    mask_buffered = mask.focal_min(radius=1.5, kernelType='circle', units='pixels')
    return image.updateMask(mask_buffered)

def get_gee_urls(year):
    """ 取得圖磚 URL (溫差異常去除版) """
    collection = (
        ee.ImageCollection("LANDSAT/LC08/C02/T1_L2")
        .filterBounds(taiwan_region)
        .filterDate(f"{year}-06-01", f"{year}-08-31")
        .filter(ee.Filter.lt('CLOUD_COVER', 60))
    )
    
    if collection.size().getInfo() == 0:
        return None, None
    
    # --- 1. 計算原始 LST (已經去掉明顯的雲) ---
    lst_raw = (
        collection.map(mask_clouds_and_scale)
        .select('ST_B10')
        .median()
        .multiply(0.00341802).add(149.0).subtract(273.15)
        .clip(taiwan_region)
    )
    
    # --- 2. 核心邏輯：空間溫差過濾 (Spatial Consistency Check) ---
    
    # A. 計算「鄰居平均溫度」 (Context)
    # 使用半徑 50 像素 (約 1.5 公里) 來代表該區域的「背景溫度」
    local_mean = lst_raw.focal_mean(radius=50, kernelType='circle', units='pixels')
    
    # B. 計算「溫差」 (Difference)
    # 溫差 = | 原始溫度 - 背景溫度 |
    diff = lst_raw.subtract(local_mean).abs()
    
  
    is_stable = diff.lte(10)
    
    # D. 應用遮罩
    # 這一行會把「原本的雲洞」加上「溫差過大的洞」合併在一起
    lst_clean = lst_raw.updateMask(is_stable)
    
    # --- 3. 聯合填補 (Interpolation) ---
    
    fill_base = lst_clean.focal_mean(radius=10, kernelType='circle', units='pixels', iterations=2)
    lst_filled = lst_clean.unmask(fill_base)
    
    lst_final = lst_filled.focal_mean(radius=1, kernelType='circle', units='pixels')
    
    # --- 4. 計算 NDVI ---
    def calc_ndvi(img):
        scaled = img.select(['SR_B5', 'SR_B4']).multiply(0.0000275).add(-0.2)
        return scaled.normalizedDifference(['SR_B5', 'SR_B4']).rename('NDVI')

    ndvi_raw = collection.map(mask_clouds_and_scale).map(calc_ndvi).median().clip(taiwan_region)
    ndvi_final = ndvi_raw.unmask(ndvi_raw.focal_mean(radius=3, units='pixels'))
    
    try:
        lst_url = lst_final.getMapId(LST_VIS)['tile_fetcher'].url_format
        ndvi_url = ndvi_final.getMapId(NDVI_VIS)['tile_fetcher'].url_format
        return lst_url, ndvi_url
    except Exception as e:
        print(f"GEE URL 生成失敗: {e}")
        return None, None

# ==========================================
# 3. Dash 前端介面設計
# ==========================================
app = dash.Dash(__name__)

# CSS 樣式
card_style = {
    'backgroundColor': 'white', 'borderRadius': '8px', 'boxShadow': '0 4px 6px rgba(0,0,0,0.1)',
    'padding': '15px', 'marginBottom': '20px', 'border': '1px solid #eee'
}

header_style = {
    'backgroundColor': '#2c3e50', 'color': 'white', 'padding': '10px',
    'textAlign': 'center', 'borderRadius': '5px 5px 0 0', 'fontWeight': 'bold'
}

app.layout = html.Div([
    html.H1("夏季都市熱島效應分析", 
            style={'textAlign': 'center', 'color': '#333', 'fontFamily': 'Arial', 'margin': '20px'}),

    html.Div([
        html.Label("請選擇年份：", style={'fontWeight': 'bold', 'fontSize': '18px'}),
        dcc.Slider(
            id='year-slider',
            min=min(years), max=max(years), step=1, value=max(years),
            marks={str(y): {'label': str(y), 'style': {'fontSize': '14px'}} for y in years},
            tooltip={"placement": "bottom", "always_visible": True}
        ),
        html.Div(id='status-msg', style={'marginTop': '15px', 'color': 'green', 'fontWeight': 'bold'})
    ], style={**card_style, 'width': '90%', 'margin': '0 auto 20px auto'}),

    html.Div([
        # 左圖：LST
        html.Div([
            html.Div("地表溫度 LST ", style={**header_style, 'backgroundColor': '#e74c3c'}),
            dl.Map(
                id="map-left",
                center=[24.0, 120.5], zoom=10,
                children=[
                    dl.TileLayer(url="https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png"),
                    dl.TileLayer(id="lst-layer", opacity=0.8)
                ],
                style={'height': '600px', 'width': '100%'}
            )
        ], style={**card_style, 'width': '48%', 'padding': '0', 'overflow': 'hidden'}),

        # 右圖：NDVI
        html.Div([
            html.Div("植生指數 NDVI (綠覆率)", style={**header_style, 'backgroundColor': '#27ae60'}),
            dl.Map(
                id="map-right",
                center=[24.0, 120.5], zoom=10,
                children=[
                    dl.TileLayer(url="https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png"),
                    dl.TileLayer(id="ndvi-layer", opacity=0.8)
                ],
                style={'height': '600px', 'width': '100%'}
            )
        ], style={**card_style, 'width': '48%', 'padding': '0', 'overflow': 'hidden'}),

    ], style={'display': 'flex', 'justifyContent': 'space-between', 'width': '95%', 'margin': '0 auto'}),

    html.P("提示：如果某區塊溫度與周圍平均溫差超過 10°C，程式會自動將其視為雲影雜訊並進行修復。", 
           style={'textAlign': 'center', 'color': '#777', 'marginTop': '20px'})

], style={'backgroundColor': '#f4f6f9', 'minHeight': '100vh', 'paddingBottom': '20px', 'fontFamily': 'Arial'})

# ==========================================
# 4. 互動邏輯
# ==========================================
@app.callback(
    [Output('lst-layer', 'url'), 
     Output('ndvi-layer', 'url'),
     Output('status-msg', 'children')],
    [Input('year-slider', 'value')]
)
def update_layers(year):
    lst_url, ndvi_url = get_gee_urls(year)
    if not lst_url:
        return "", "", f"⚠️ {year} 年夏季無可用影像"
    return lst_url, ndvi_url, f"✅ {year} 年影像載入完成 "

@app.callback(Output('map-right', 'viewport'), Input('map-left', 'viewport'))
def sync_left_to_right(viewport): return viewport

@app.callback(Output('map-left', 'viewport'), Input('map-right', 'viewport'))
def sync_right_to_left(viewport): return viewport

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=7860, debug=False)