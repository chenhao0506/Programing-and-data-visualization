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
# 2. GEE 參數與數據處理函數
# ----------------------------------------------------
taiwan_region = ee.Geometry.Rectangle([120.24, 23.77, 120.69, 24.20])
years = list(range(2015, 2026))

# LST 可視化 (熱圖)
LST_VIS = {'min': 20, 'max': 45, 'palette': ['blue', 'cyan', 'green', 'yellow', 'red']}
# NDVI 可視化 (綠色調)
NDVI_VIS = {'min': 0, 'max': 0.7, 'palette': ['#FFFFFF', '#CE7E45', '#F1B555', '#66A000', '#056201']}

def get_gee_layers(year):
    collection = (ee.ImageCollection("LANDSAT/LC08/C02/T1_L2")
                  .filterBounds(taiwan_region)
                  .filterDate(f"{year}-06-01", f"{year}-08-31")
                  .filter(ee.Filter.lt('CLOUD_COVER', 30)))
    
    if collection.size().getInfo() == 0:
        return None, None
    
    img = collection.median().clip(taiwan_region)
    
    # 計算 LST (攝氏度)
    lst = img.select('ST_B10').multiply(0.00341802).add(149.0).subtract(273.15)
    
    # 計算 NDVI
    # 
    nir = img.select('SR_B5').multiply(0.0000275).add(-0.2)
    red = img.select('SR_B4').multiply(0.0000275).add(-0.2)
    ndvi = nir.subtract(red).divide(nir.add(red))
    
    return lst.getMapId(LST_VIS)['tile_fetcher'].url_format, \
           ndvi.getMapId(NDVI_VIS)['tile_fetcher'].url_format

# ----------------------------------------------------
# 3. Dash App 佈局
# ----------------------------------------------------
app = dash.Dash(__name__)

app.layout = html.Div([
    html.H2("Landsat 8 捲簾對比：LST 溫度 (左) vs NDVI 植生 (右)", 
            style={'textAlign': 'center', 'fontFamily': 'Arial'}),
    
    html.Div([
        dcc.Slider(
            id='year-slider',
            min=min(years), max=max(years), step=1, value=max(years),
            marks={str(y): str(y) for y in years},
        ),
    ], style={'padding': '20px', 'width': '80%', 'margin': 'auto'}),

    html.Div([
        dl.Map(
            id="comparison-map",
            center=[24.0, 120.5], 
            zoom=11,
            children=[
                # 標準底圖
                dl.TileLayer(url="https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png"),
                
                # 捲簾控制元件 (關鍵：ID 必須匹配)
                dl.SideBySideControl(
                    id="sbs-control",
                    leftLayerId="left-lst", 
                    rightLayerId="right-ndvi"
                ),
                
                # 用來存放更新後的圖層容器
                html.Div(id="layers-container")
            ],
            style={'width': '100%', 'height': '70vh'}
        ),
        # 左右圖層說明文字
        html.Div("← 夏季地表溫度 (LST)", style={'position': 'absolute', 'top': '10px', 'left': '50px', 'zIndex': '1000', 'background': 'white', 'padding': '5px', 'borderRadius': '5px'}),
        html.Div("植生指數 (NDVI) →", style={'position': 'absolute', 'top': '10px', 'right': '50px', 'zIndex': '1000', 'background': 'white', 'padding': '5px', 'borderRadius': '5px'}),
    ], style={'width': '90%', 'margin': 'auto', 'position': 'relative'})
])

# ----------------------------------------------------
# 4. Callback：動態載入 GEE Tile
# ----------------------------------------------------
@app.callback(
    Output('layers-container', 'children'),
    Input('year-slider', 'value')
)
def update_layers(year):
    lst_url, ndvi_url = get_gee_layers(year)
    
    if not lst_url:
        return []

    # 這裡的 id 必須與 SideBySideControl 的參數完全相同
    return [
        dl.TileLayer(url=lst_url, id="left-lst", opacity=1.0),
        dl.TileLayer(url=ndvi_url, id="right-ndvi", opacity=1.0)
    ]

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=7860, debug=False)