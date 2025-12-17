import os
import json
import ee
import dash
from dash import dcc, html, Output, Input, State
import dash_leaflet as dl
import dash_bootstrap_components as dbc
from google.oauth2 import service_account

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
# 2. GEE 參數與數據處理
# ----------------------------------------------------
taiwan_region = ee.Geometry.Rectangle([120.24, 23.77, 120.69, 24.20])
years = list(range(2015, 2026))

LST_VIS = {'min': 15, 'max': 45, 'palette': ['040274', '0402ff', '3be285', 'ffd611', 'ff0000', '911003']}
NDVI_VIS = {'min': 0, 'max': 0.8, 'palette': ['FFFFFF', 'CE7E45', 'F1B555', '66A000', '207401', '056201', '004C00']}

def get_gee_layers(year):
    collection = (ee.ImageCollection("LANDSAT/LC08/C02/T1_L2")
                  .filterBounds(taiwan_region)
                  .filterDate(f"{year}-06-01", f"{year}-08-31")
                  .filter(ee.Filter.lt('CLOUD_COVER', 40)))
    if collection.size().getInfo() == 0:
        return None, None
    img = collection.median().clip(taiwan_region)
    
    # LST: 地表溫度
    lst = img.select('ST_B10').multiply(0.00341802).add(149.0).subtract(273.15)
    
    # NDVI: 植生指數 (B5 - B4) / (B5 + B4)
    # 
    nir = img.select('SR_B5').multiply(0.0000275).add(-0.2)
    red = img.select('SR_B4').multiply(0.0000275).add(-0.2)
    ndvi = nir.subtract(red).divide(nir.add(red))
    
    return lst.getMapId(LST_VIS)['tile_fetcher'].url_format, \
           ndvi.getMapId(NDVI_VIS)['tile_fetcher'].url_format

# ----------------------------------------------------
# 3. Dash App 佈局 (使用 DBC)
# ----------------------------------------------------
app = dash.Dash(__name__, external_stylesheets=[dbc.themes.FLATLY])

app.layout = dbc.Container([
    dbc.Row([
        dbc.Col(html.H2("夏季都市熱島對比分析：LST vs NDVI", className="text-center my-4"), width=12)
    ]),
    
    dbc.Row([
        dbc.Col([
            dbc.Card([
                dbc.CardHeader(f"分析參數設定"),
                dbc.CardBody([
                    html.P("選擇年份："),
                    dcc.Slider(
                        id='year-slider',
                        min=min(years), max=max(years), step=1, value=max(years),
                        marks={str(y): str(y) for y in years},
                        className="mb-4"
                    ),
                    html.Div([
                        dbc.Badge("左側：LST 溫度 (攝氏)", color="danger", className="me-2"),
                        dbc.Badge("右側：NDVI 植生指數", color="success"),
                    ], className="text-center")
                ])
            ], className="mb-4")
        ], width=12)
    ]),

    dbc.Row([
        dbc.Col([
            html.Div([
                dl.Map(
                    center=[24.0, 120.5], 
                    zoom=11,
                    children=[
                        dl.TileLayer(url="https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png"),
                        # 注意：某些版本中使用 SideBySide，如果報錯請確認 dash-leaflet 版本
                        # 若版本大於 1.0，API 結構可能需調整，此為最通用寫法
                        dl.SideBySide(id="sbs", leftLayerId="left-layer", rightLayerId="right-layer"),
                        html.Div(id="layers-container")
                    ],
                    style={'width': '100%', 'height': '600px', 'borderRadius': '10px'}
                )
            ], style={'position': 'relative'})
        ], width=12)
    ]),
    
    dbc.Row([
        dbc.Col(html.P("數據來源：Landsat 8 Collection 2 Level 2", className="text-muted mt-2 small"), width=12)
    ])
], fluid=True)

# ----------------------------------------------------
# 4. Callback 更新
# ----------------------------------------------------
@app.callback(
    Output('layers-container', 'children'),
    Input('year-slider', 'value')
)
def update_map(year):
    lst_url, ndvi_url = get_gee_layers(year)
    if not lst_url:
        return []

    # 必須確保 ID 與 SideBySide 的 leftLayerId/rightLayerId 一致
    return [
        dl.TileLayer(url=lst_url, id="left-layer"),
        dl.TileLayer(url=ndvi_url, id="right-layer")
    ]

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=7860, debug=False)