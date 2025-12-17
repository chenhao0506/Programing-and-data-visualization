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
# 2. GEE 參數與函數
# ----------------------------------------------------
taiwan_region = ee.Geometry.Rectangle([120.24, 23.77, 120.69, 24.20])
years = list(range(2015, 2026))

LST_VIS = {'min': 15, 'max': 45, 'palette': ['040274', '0402ff', '3be285', 'ffd611', 'ff0000', '911003']}
# 
NDVI_VIS = {'min': 0, 'max': 0.8, 'palette': ['FFFFFF', 'CE7E45', 'F1B555', '66A000', '207401', '056201', '004C00']}

def get_l8_data(year):
    collection = (ee.ImageCollection("LANDSAT/LC08/C02/T1_L2")
                  .filterBounds(taiwan_region)
                  .filterDate(f"{year}-06-01", f"{year}-08-31")
                  .filter(ee.Filter.lt('CLOUD_COVER', 40)))
    if collection.size().getInfo() == 0:
        return None, None
    img = collection.median().clip(taiwan_region)
    lst = img.select('ST_B10').multiply(0.00341802).add(149.0).subtract(273.15).rename('LST')
    nir = img.select('SR_B5').multiply(0.0000275).add(-0.2)
    red = img.select('SR_B4').multiply(0.0000275).add(-0.2)
    ndvi = nir.subtract(red).divide(nir.add(red)).rename('NDVI')
    return lst, ndvi

# ----------------------------------------------------
# 3. Dash App 佈局
# ----------------------------------------------------
app = dash.Dash(__name__)

app.layout = html.Div([
    html.H1("Landsat 8: LST 溫度 (左) vs NDVI 植生 (右)", 
            style={'textAlign': 'center', 'color': '#2C3E50', 'fontFamily': 'sans-serif'}),
    
    html.Div([
        html.H3(id='year-display', style={'textAlign': 'center'}),
        dcc.Slider(
            id='year-slider',
            min=min(years), max=max(years), step=1, value=max(years),
            marks={str(y): str(y) for y in years}
        ),
    ], style={'padding': '20px', 'backgroundColor': '#f8f9fa', 'borderRadius': '10px', 'width': '80%', 'margin': '0 auto'}),

    html.Div([
        dl.Map(
            id="leaflet-map",
            center=[23.98, 120.46], 
            zoom=11,
            children=[
                dl.TileLayer(url="https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png"),
                
                # 建立兩個 Pane 用於 SideBySideControl
                dl.Pane(id="left-pane", name="left-pane", style={"zIndex": 500}),
                dl.Pane(id="right-pane", name="right-pane", style={"zIndex": 501}),
                
                # 放置圖層的容器
                html.Div(id="gee-layers-container"),
                
                # 正確的 SideBySideControl 元件
                dl.SideBySideControl(id="sbs", leftLayerId="left-layer", rightLayerId="right-layer")
            ],
            style={'width': '100%', 'height': '600px', 'marginTop': '20px'}
        ),
        # 標籤提示
        html.Div("← LST (左)", style={'position': 'absolute', 'bottom': '30px', 'left': '20px', 'zIndex': '1000', 'background': 'white', 'padding': '5px', 'border': '1px solid black'}),
        html.Div("NDVI (右) →", style={'position': 'absolute', 'bottom': '30px', 'right': '20px', 'zIndex': '1000', 'background': 'white', 'padding': '5px', 'border': '1px solid black'}),
    ], style={'width': '80%', 'margin': '0 auto', 'position': 'relative'})
])

# ----------------------------------------------------
# 4. Callback 更新
# ----------------------------------------------------
@app.callback(
    [Output('gee-layers-container', 'children'),
     Output('year-display', 'children')],
    [Input('year-slider', 'value')]
)
def update_map(selected_year):
    lst_img, ndvi_img = get_l8_data(selected_year)
    
    if lst_img is None:
        return [], f"年份: {selected_year} (無影像資料)"

    lst_url = lst_img.getMapId(LST_VIS)['tile_fetcher'].url_format
    ndvi_url = ndvi_img.getMapId(NDVI_VIS)['tile_fetcher'].url_format

    # 將圖層指定到對應的 Pane 中
    new_layers = [
        dl.TileLayer(url=lst_url, id="left-layer", pane="left-pane"),
        dl.TileLayer(url=ndvi_url, id="right-layer", pane="right-pane")
    ]

    return new_layers, f"當前分析年份: {selected_year}"

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=7860, debug=False)