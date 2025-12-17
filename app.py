import os
import json
import ee
import dash
import dash_leaflet as dl
import dash_bootstrap_components as dbc
from dash import dcc, html, Output, Input, State
from google.oauth2 import service_account
from typing import List

# ----------------------------------------------------
# 1. Earth Engine 初始化
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
# 2. 參數設定與圖例生成函數
# ----------------------------------------------------
LST_VIS = {
    'min': 20, 
    'max': 45, 
    'palette': ['040274', '0402ff', '3be285', 'ffd611', 'ff0000', '911003']
}

NDVI_VIS = {
    'min': 0.0, 
    'max': 0.8, 
    'palette': ['FFFFFF', 'CE7E45', 'F1B555', '66A000', '207401', '056201', '004C00']
}

def create_complete_legend(title: str, min_val: float, max_val: float, palette: List[str], unit: str) -> html.Div:
    num_colors = len(palette)
    block_height = 25 
    
    color_blocks = [
        html.Div(style={
            'backgroundColor': f'#{color}' if not str(color).startswith('#') else color,
            'height': f'{block_height}px', 'width': '20px', 'borderLeft': '1px solid #333', 'borderRight': '1px solid #333'
        }) for color in reversed(palette)
    ]
    color_blocks[0].style['borderTop'] = '1px solid #333'
    color_blocks[-1].style['borderBottom'] = '1px solid #333'

    labels = []
    num_labels = 5
    total_height = num_colors * block_height
    for i in range(num_labels):
        val = max_val - i * (max_val - min_val) / (num_labels - 1)
        top_pos = i * (total_height - block_height) / (num_labels - 1)
        labels.append(html.Div(
            f"{val:.1f}{unit}",
            style={
                'position': 'absolute', 'top': f'{top_pos + 5}px', 
                'left': '28px', 'fontSize': '11px', 'color': '#333', 'whiteSpace': 'nowrap'
            }
        ))

    return html.Div(
        style={
            'position': 'absolute', 'top': '10px', 'right': '10px', 'zIndex': 1000,
            'backgroundColor': 'rgba(255, 255, 255, 0.9)', 'padding': '10px',
            'borderRadius': '5px', 'boxShadow': '0 2px 4px rgba(0,0,0,0.2)',
            'width': '90px'
        },
        children=[
            html.Div(title, style={'fontSize': '13px', 'fontWeight': 'bold', 'marginBottom': '10px', 'textAlign': 'center'}),
            html.Div(
                style={'position': 'relative', 'height': f'{total_height + 20}px'},
                children=[
                    html.Div(color_blocks),
                    html.Div(labels)
                ]
            )
        ]
    )

# ----------------------------------------------------
# 3. GEE 數據處理函數 (效能優化版)
# ----------------------------------------------------
taiwan_region = ee.Geometry.Rectangle([120.24, 23.77, 120.69, 24.20])
years = list(range(2015, 2026))

def mask_clouds_and_scale(image):
    qa = image.select('QA_PIXEL')
    mask = qa.bitwiseAnd(1 << 3).eq(0).And(qa.bitwiseAnd(1 << 4).eq(0))
    mask_buffered = mask.focal_min(radius=1.5, kernelType='circle', units='pixels')
    return image.updateMask(mask_buffered)

def get_gee_urls(year):
    collection = (
        ee.ImageCollection("LANDSAT/LC08/C02/T1_L2")
        .filterBounds(taiwan_region)
        .filterDate(f"{year}-06-01", f"{year}-08-31")
        .filter(ee.Filter.lt('CLOUD_COVER', 60))
    )
    
    if collection.size().getInfo() == 0:
        return None, None
    
    # 先做 median 合併，後續所有複雜空間運算只跑一次
    base_img = collection.map(mask_clouds_and_scale).median().clip(taiwan_region)
    
    # --- 1. LST 處理 ---
    lst_raw = (
        base_img.select('ST_B10')
        .multiply(0.00341802).add(149.0).subtract(273.15)
    )
    
    # 空間溫差過濾 (Spatial Consistency Check)
    local_mean = lst_raw.focal_mean(radius=40, kernelType='circle', units='pixels')
    is_stable = lst_raw.subtract(local_mean).abs().lte(10)
    lst_clean = lst_raw.updateMask(is_stable)
    
    # 填補 (保留 iteration=2)
    fill_base = lst_clean.focal_mean(radius=10, kernelType='circle', units='pixels', iterations=2)
    lst_filled = lst_clean.unmask(fill_base)
    lst_final = lst_filled.focal_mean(radius=1, kernelType='circle', units='pixels')
    
    # --- 2. NDVI 處理 ---
    scaled_bands = base_img.select(['SR_B5', 'SR_B4']).multiply(0.0000275).add(-0.2)
    ndvi_raw = scaled_bands.normalizedDifference(['SR_B5', 'SR_B4']).rename('NDVI')
    ndvi_final = ndvi_raw.unmask(ndvi_raw.focal_mean(radius=3, units='pixels'))
    
    try:
        lst_url = lst_final.getMapId(LST_VIS)['tile_fetcher'].url_format
        ndvi_url = ndvi_final.getMapId(NDVI_VIS)['tile_fetcher'].url_format
        return lst_url, ndvi_url
    except Exception as e:
        print(f"GEE URL 生成失敗: {e}")
        return None, None

# ----------------------------------------------------
# 4. Dash 前端介面設計
# ----------------------------------------------------
app = dash.Dash(__name__, external_stylesheets=[dbc.themes.BOOTSTRAP])

card_style = {
    'backgroundColor': 'white', 'borderRadius': '8px', 'boxShadow': '0 4px 6px rgba(0,0,0,0.1)',
    'padding': '15px', 'marginBottom': '20px', 'border': '1px solid #eee'
}

header_style = {
    'padding': '10px', 'textAlign': 'center', 'borderRadius': '5px 5px 0 0', 'fontWeight': 'bold', 'color': 'white'
}

app.layout = html.Div([
    html.H1("2015-2025 台灣中部 LST 與 NDVI 時空對照平台", 
            style={'textAlign': 'center', 'margin': '20px', 'color': '#333', 'fontFamily': 'Arial'}),

    html.Div([
        html.Label("請選擇年份：", style={'fontWeight': 'bold', 'fontSize': '18px'}),
        dcc.Slider(
            id='year-slider', min=min(years), max=max(years), step=1, value=max(years),
            marks={str(y): str(y) for y in years},
            tooltip={"placement": "bottom", "always_visible": True}
        ),
        html.Div(id='status-msg', style={'marginTop': '15px', 'color': 'green', 'fontWeight': 'bold'})
    ], style={**card_style, 'width': '90%', 'margin': '0 auto 20px auto'}),

    html.Div([
        # 左圖：LST
        html.Div([
            html.Div("地表溫度 LST", style={**header_style, 'backgroundColor': '#e74c3c'}),
            dl.Map(
                id="map-left", center=[24.0, 120.5], zoom=10,
                children=[
                    dl.TileLayer(url="https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png"),
                    dl.TileLayer(id="lst-layer", opacity=0.8),
                    create_complete_legend("LST", LST_VIS['min'], LST_VIS['max'], LST_VIS['palette'], "°C")
                ],
                style={'height': '600px', 'width': '100%'}
            )
        ], style={**card_style, 'width': '48%', 'padding': '0', 'overflow': 'hidden'}),

        # 右圖：NDVI
        html.Div([
            html.Div("植生指數 NDVI", style={**header_style, 'backgroundColor': '#27ae60'}),
            dl.Map(
                id="map-right", center=[24.0, 120.5], zoom=10,
                children=[
                    dl.TileLayer(url="https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png"),
                    dl.TileLayer(id="ndvi-layer", opacity=0.8),
                    create_complete_legend("NDVI", NDVI_VIS['min'], NDVI_VIS['max'], NDVI_VIS['palette'], "")
                ],
                style={'height': '600px', 'width': '100%'}
            )
        ], style={**card_style, 'width': '48%', 'padding': '0', 'overflow': 'hidden'}),

    ], style={'display': 'flex', 'justifyContent': 'space-between', 'width': '95%', 'margin': '0 auto'}),

    html.P("提示：如果某區塊溫度與周圍平均溫差超過 10°C，程式會自動將其視為雲影雜訊並進行修復。", 
           style={'textAlign': 'center', 'color': '#777', 'marginTop': '20px', 'fontSize': '14px'})

], style={'backgroundColor': '#f4f6f9', 'minHeight': '100vh', 'paddingBottom': '20px', 'fontFamily': 'Arial'})

# ----------------------------------------------------
# 5. 互動邏輯
# ----------------------------------------------------
@app.callback(
    [Output('lst-layer', 'url'), Output('ndvi-layer', 'url'), Output('status-msg', 'children')],
    [Input('year-slider', 'value')]
)
def update_layers(year):
    lst_url, ndvi_url = get_gee_urls(year)
    if not lst_url:
        return "", "", f" {year} 年夏季無可用影像"
    return lst_url, ndvi_url, f" {year} 年影像載入完成"

@app.callback(Output('map-right', 'viewport'), Input('map-left', 'viewport'))
def sync_left_to_right(viewport): return viewport

@app.callback(Output('map-left', 'viewport'), Input('map-right', 'viewport'))
def sync_right_to_left(viewport): return viewport

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=7860, debug=False)