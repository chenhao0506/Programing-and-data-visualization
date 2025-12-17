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
# 1. Earth Engine 初始化 (保持原樣)
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

def create_map_legend(title: str, min_val: float, max_val: float, palette: List[str], unit: str) -> html.Div:
    """
    通用圖例生成函數，生成一個垂直色帶與對應刻度
    """
    num_colors = len(palette)
    # 建立色帶塊 (從上到下：熱 -> 冷 / 高 -> 低)
    color_blocks = [
        html.Div(style={
            'backgroundColor': f'#{palette[i]}' if not palette[i].startswith('#') else palette[i],
            'height': '20px', 'width': '20px'
        }) for i in reversed(range(num_colors))
    ]

    # 建立刻度標籤 (顯示 5 個點)
    labels = []
    num_labels = 5
    for i in range(num_labels):
        val = max_val - i * (max_val - min_val) / (num_labels - 1)
        # 計算 top 位置 (色帶總高度是 num_colors * 20px)
        top_pos = i * (num_colors - 1) * 20 / (num_labels - 1)
        labels.append(html.Div(
            f"{val:.1f}{unit}",
            style={
                'position': 'absolute', 'top': f'{top_pos - 7}px', 
                'left': '28px', 'fontSize': '11px', 'color': '#333', 'whiteSpace': 'nowrap'
            }
        ))

    return html.Div(
        style={
            'position': 'absolute', 'top': '10px', 'right': '10px', 'zIndex': 1000,
            'backgroundColor': 'rgba(255, 255, 255, 0.85)', 'padding': '10px',
            'borderRadius': '5px', 'boxShadow': '0 2px 4px rgba(0,0,0,0.2)',
            'width': '85px'
        },
        children=[
            html.Div(title, style={'fontSize': '12px', 'fontWeight': 'bold', 'marginBottom': '8px', 'textAlign': 'center'}),
            html.Div(
                style={'position': 'relative', 'height': f'{num_colors * 20}px'},
                children=[
                    html.Div(color_blocks, style={'border': '1px solid #666', 'width': '20px'}),
                    html.Div(labels)
                ]
            )
        ]
    )

# ----------------------------------------------------
# 3. GEE 數據處理函數 (保持原樣)
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
    
    lst_raw = collection.map(mask_clouds_and_scale).select('ST_B10').median().multiply(0.00341802).add(149.0).subtract(273.15).clip(taiwan_region)
    local_mean = lst_raw.focal_mean(radius=50, kernelType='circle', units='pixels')
    is_stable = lst_raw.subtract(local_mean).abs().lte(10)
    lst_clean = lst_raw.updateMask(is_stable)
    lst_final = lst_clean.unmask(lst_clean.focal_mean(radius=10, iterations=2)).focal_mean(radius=1)

    def calc_ndvi(img):
        scaled = img.select(['SR_B5', 'SR_B4']).multiply(0.0000275).add(-0.2)
        return scaled.normalizedDifference(['SR_B5', 'SR_B4']).rename('NDVI')

    ndvi_raw = collection.map(mask_clouds_and_scale).map(calc_ndvi).median().clip(taiwan_region)
    ndvi_final = ndvi_raw.unmask(ndvi_raw.focal_mean(radius=3, units='pixels'))
    
    try:
        lst_url = lst_final.getMapId(LST_VIS)['tile_fetcher'].url_format
        ndvi_url = ndvi_final.getMapId(NDVI_VIS)['tile_fetcher'].url_format
        return lst_url, ndvi_url
    except:
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
    html.H1("2015-2025 台灣中部 LST 與 NDVI 時空對照", 
            style={'textAlign': 'center', 'margin': '20px', 'color': '#2c3e50'}),

    html.Div([
        html.Label("年份選擇：", style={'fontWeight': 'bold'}),
        dcc.Slider(
            id='year-slider', min=min(years), max=max(years), step=1, value=max(years),
            marks={str(y): str(y) for y in years},
            tooltip={"placement": "bottom", "always_visible": True}
        ),
        html.Div(id='status-msg', style={'marginTop': '10px', 'fontSize': '14px'})
    ], style={**card_style, 'width': '90%', 'margin': '0 auto 20px auto'}),

    html.Div([
        # 左圖：LST + 圖例
        html.Div([
            html.Div("地表溫度 (LST)", style={**header_style, 'backgroundColor': '#e74c3c'}),
            dl.Map(
                id="map-left", center=[24.0, 120.5], zoom=10,
                children=[
                    dl.TileLayer(url="https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png"),
                    dl.TileLayer(id="lst-layer", opacity=0.8),
                    create_map_legend("LST", LST_VIS['min'], LST_VIS['max'], LST_VIS['palette'], "°C")
                ],
                style={'height': '600px', 'width': '100%'}
            )
        ], style={**card_style, 'width': '49%', 'padding': '0'}),

        # 右圖：NDVI + 圖例
        html.Div([
            html.Div("植生指數 (NDVI)", style={**header_style, 'backgroundColor': '#27ae60'}),
            dl.Map(
                id="map-right", center=[24.0, 120.5], zoom=10,
                children=[
                    dl.TileLayer(url="https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png"),
                    dl.TileLayer(id="ndvi-layer", opacity=0.8),
                    create_map_legend("NDVI", NDVI_VIS['min'], NDVI_VIS['max'], NDVI_VIS['palette'], "")
                ],
                style={'height': '600px', 'width': '100%'}
            )
        ], style={**card_style, 'width': '49%', 'padding': '0'}),

    ], style={'display': 'flex', 'justifyContent': 'space-around', 'padding': '0 20px'})
], style={'backgroundColor': '#f8f9fa', 'minHeight': '100vh'})

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
        return "", "", f" {year} 年影像不可用"
    return lst_url, ndvi_url, f" 已載入 {year} 年數據"

@app.callback(Output('map-right', 'viewport'), Input('map-left', 'viewport'))
def sync_left_to_right(viewport): return viewport

@app.callback(Output('map-left', 'viewport'), Input('map-right', 'viewport'))
def sync_right_to_left(viewport): return viewport

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=7860, debug=False)