import os
import json
import ee
import dash
from dash import dcc, html, Output, Input, State
import dash_leaflet as dl
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
# 2. 參數與 GEE 函數定義
# ----------------------------------------------------
taiwan_region = ee.Geometry.Rectangle([120.24, 23.77, 120.69, 24.20])
years = list(range(2015, 2026))

# 可視化參數
LST_VIS = {'min': 10, 'max': 45, 'palette': ['040274', '0402ff', '3be285', 'ffd611', 'ff0000', '911003']}
NDVI_VIS = {'min': 0, 'max': 0.8, 'palette': ['FFFFFF', 'CE7E45', 'DF923D', 'F1B555', 'FCD163', '99B718', '74A901', '66A000', '529400', '3E8601', '207401', '056201', '004C00', '023B01', '012E01', '011D01', '011301']}

def get_l8_summer_data(year):
    """取得該年度夏季的 LST 與 NDVI"""
    collection = (ee.ImageCollection("LANDSAT/LC08/C02/T1_L2")
                  .filterBounds(taiwan_region)
                  .filterDate(f"{year}-06-01", f"{year}-08-31")
                  .filter(ee.Filter.lt('CLOUD_COVER', 40)))
    
    if collection.size().getInfo() == 0:
        return None, None
    
    img = collection.median().clip(taiwan_region)
    
    # 1. 計算 LST
    lst = img.select('ST_B10').multiply(0.00341802).add(149.0).subtract(273.15)
    
    # 2. 計算 NDVI
    # 
    # NDVI = (NIR - RED) / (NIR + RED)
    # Landsat 8: NIR=B5, RED=B4
    nir = img.select('SR_B5').multiply(0.0000275).add(-0.2)
    red = img.select('SR_B4').multiply(0.0000275).add(-0.2)
    ndvi = nir.subtract(red).divide(nir.add(red)).rename('NDVI')
    
    return lst, ndvi

# ----------------------------------------------------
# 3. Dash App 佈局
# ----------------------------------------------------
app = dash.Dash(__name__)

app.layout = html.Div([
    html.H1("地表溫度 (LST) vs 植生指數 (NDVI) 對比分析", style={'textAlign': 'center'}),
    
    html.Div([
        dcc.Slider(id='year-slider', min=min(years), max=max(years), step=1, value=max(years),
                   marks={str(y): str(y) for y in years}),
    ], style={'padding': '20px'}),

    html.Div([
        dl.Map(
            id="leaflet-map",
            center=[24.0, 120.5], 
            zoom=10,
            children=[
                dl.TileLayer(url="https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png", zIndex=1),
                # 初始預留空圖層群組，待 Callback 填充
                dl.LayerGroup(id="left-layer"),
                dl.LayerGroup(id="right-layer"),
                # SplitControl 將 leftLayerId 設為左邊，rightLayerId 設為右邊
                dl.SplitControl(id="split-control", leftLayerId="left-layer", rightLayerId="right-layer")
            ],
            style={'width': '100%', 'height': '600px'}
        )
    ], style={'width': '90%', 'margin': 'auto', 'position': 'relative'})
])

# ----------------------------------------------------
# 4. Callback 更新圖層
# ----------------------------------------------------
@app.callback(
    [Output('left-layer', 'children'),
     Output('right-layer', 'children')],
    [Input('year-slider', 'value')]
)
def update_split_map(selected_year):
    lst_img, ndvi_img = get_l8_summer_data(selected_year)
    
    if lst_img is None:
        return [], []

    # 取得 LST Tile URL
    lst_url = lst_img.getMapId(LST_VIS)['tile_fetcher'].url_format
    # 取得 NDVI Tile URL
    ndvi_url = ndvi_img.getMapId(NDVI_VIS)['tile_fetcher'].url_format

    # 左側顯示 LST
    left_children = [
        dl.TileLayer(url=lst_url, attribution="GEE LST", opacity=0.9),
        html.Div("左：地表溫度 (LST)", style={'position': 'absolute', 'bottom': '20px', 'left': '20px', 
                                            'zIndex': '1000', 'backgroundColor': 'white', 'padding': '5px'})
    ]

    # 右側顯示 NDVI
    right_children = [
        dl.TileLayer(url=ndvi_url, attribution="GEE NDVI", opacity=0.9),
        html.Div("右：植生指數 (NDVI)", style={'position': 'absolute', 'bottom': '20px', 'right': '20px', 
                                              'zIndex': '1000', 'backgroundColor': 'white', 'padding': '5px'})
    ]

    return left_children, right_children

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=7860, debug=False)