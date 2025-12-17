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
taiwan_region = ee.Geometry.Rectangle([120.24, 23.77, 120.69, 24.20])
years = list(range(2015, 2026))

LST_VIS = {'min': 20, 'max': 45, 'palette': ['040274', '0402ff', '3be285', 'ffd611', 'ff0000', '911003']}
NDVI_VIS = {'min': 0, 'max': 0.8, 'palette': ['FFFFFF', 'CE7E45', 'F1B555', '66A000', '207401', '056201', '004C00']}

def get_gee_urls(year):
    collection = (ee.ImageCollection("LANDSAT/LC08/C02/T1_L2")
                  .filterBounds(taiwan_region)
                  .filterDate(f"{year}-06-01", f"{year}-08-31")
                  .filter(ee.Filter.lt('CLOUD_COVER', 40)))
    
    if collection.size().getInfo() == 0:
        return None, None
        
    img = collection.median().clip(taiwan_region)
    
    # 計算 LST
    lst = img.select('ST_B10').multiply(0.00341802).add(149.0).subtract(273.15)
    
    # 計算 NDVI
    nir = img.select('SR_B5').multiply(0.0000275).add(-0.2)
    red = img.select('SR_B4').multiply(0.0000275).add(-0.2)
    ndvi = nir.subtract(red).divide(nir.add(red))
    
    return lst.getMapId(LST_VIS)['tile_fetcher'].url_format, \
           ndvi.getMapId(NDVI_VIS)['tile_fetcher'].url_format

# ----------------------------------------------------
# 3. Dash App 佈局 (DBC 左右對比)
# ----------------------------------------------------
app = dash.Dash(__name__, external_stylesheets=[dbc.themes.FLATLY])

app.layout = dbc.Container([
    dbc.Row([
        dbc.Col(html.H2("夏季都市熱島同步對比分析", className="text-center my-4 text-primary"), width=12)
    ]),
    
    dbc.Row([
        dbc.Col([
            dbc.Card([
                dbc.CardBody([
                    html.Label("選擇分析年份：", className="fw-bold"),
                    dcc.Slider(
                        id='year-slider',
                        min=min(years), max=max(years), step=1, value=max(years),
                        marks={str(y): str(y) for y in years},
                        className="mb-3"
                    ),
                    html.Div(id='status-msg', className="text-muted small")
                ])
            ], className="mb-4 shadow-sm")
        ], width=12)
    ]),

    # 核心：左右對比地圖
    dbc.Row([
        # 左側地圖：LST
        dbc.Col([
            dbc.Card([
                dbc.CardHeader("地表溫度 LST (°C)", className="bg-danger text-white text-center fw-bold"),
                dl.Map(
                    id="map-left",
                    center=[24.0, 120.5], 
                    zoom=11,
                    children=[
                        dl.TileLayer(url="https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png"),
                        dl.TileLayer(id="lst-layer", opacity=0.8),
                    ],
                    style={'height': '550px'}
                )
            ], className="shadow")
        ], width=6),
        
        # 右側地圖：NDVI
        dbc.Col([
            dbc.Card([
                dbc.CardHeader("植生指數 NDVI", className="bg-success text-white text-center fw-bold"),
                dl.Map(
                    id="map-right",
                    center=[24.0, 120.5], 
                    zoom=11,
                    children=[
                        dl.TileLayer(url="https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png"),
                        dl.TileLayer(id="ndvi-layer", opacity=0.8),
                    ],
                    style={'height': '550px'}
                )
            ], className="shadow")
        ], width=6),
    ]),
    
    dbc.Row([
        dbc.Col(html.P("提示：移動任何一邊地圖，另一邊會自動同步視角。", className="text-center mt-3 text-muted"), width=12)
    ])
], fluid=True)

# ----------------------------------------------------
# 4. 回調函數 (Callbacks)
# ----------------------------------------------------

# 更新 GEE 圖層
@app.callback(
    [Output('lst-layer', 'url'), 
     Output('ndvi-layer', 'url'),
     Output('status-msg', 'children')],
    [Input('year-slider', 'value')]
)
def update_layers(year):
    lst_url, ndvi_url = get_gee_urls(year)
    if not lst_url:
        return "", "", f"⚠️ {year} 年份數據不足（雲層過多）"
    return lst_url, ndvi_url, f"✅ 已載入 {year} 年影像數據"

# 同步視角：左到右
@app.callback(
    Output('map-right', 'viewport'),
    Input('map-left', 'viewport')
)
def sync_left_to_right(viewport):
    return viewport

# 同步視角：右到左
@app.callback(
    Output('map-left', 'viewport'),
    Input('map-right', 'viewport')
)
def sync_right_to_left(viewport):
    return viewport

if __name__ == "__main__":
    # 請確保已安裝 pip install dash-bootstrap-components
    app.run(host="0.0.0.0", port=7860, debug=False)