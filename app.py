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

LST_VIS = {'min': 10, 'max': 45, 'palette': ['040274', '0402ff', '3be285', 'ffd611', 'ff0000', '911003']}
NDVI_VIS = {'min': 0, 'max': 0.8, 'palette': ['FFFFFF', 'CE7E45', 'F1B555', '66A000', '207401', '056201', '004C00']}

def get_l8_data(year):
    collection = (ee.ImageCollection("LANDSAT/LC08/C02/T1_L2")
                  .filterBounds(taiwan_region)
                  .filterDate(f"{year}-06-01", f"{year}-08-31")
                  .filter(ee.Filter.lt('CLOUD_COVER', 40)))
    
    if collection.size().getInfo() == 0:
        return None, None
    
    img = collection.median().clip(taiwan_region)
    
    # 計算 LST
    lst = img.select('ST_B10').multiply(0.00341802).add(149.0).subtract(273.15).rename('LST')
    
    # 計算 NDVI
    # Landsat 8: NDVI = (B5 - B4) / (B5 + B4)
    nir = img.select('SR_B5').multiply(0.0000275).add(-0.2)
    red = img.select('SR_B4').multiply(0.0000275).add(-0.2)
    ndvi = nir.subtract(red).divide(nir.add(red)).rename('NDVI')
    
    return lst, ndvi

# ----------------------------------------------------
# 3. Dash App 佈局
# ----------------------------------------------------
app = dash.Dash(__name__)

app.layout = html.Div([
    html.H1("Landsat 8: LST (左) vs NDVI (右) 對比分析", 
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
                # 基礎底圖
                dl.TileLayer(url="https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png", zIndex=1),
                
                # SideBySide 控制左右對比 (這取代了 SplitControl)
                # leftLayer 會放在左邊，rightLayer 放在右邊
                dl.SideBySide(id="side-by-side", leftLayerId="left-layer", rightLayerId="right-layer"),
                
                # 用來放置 GEE 圖層的容器
                html.Div(id="gee-layers-container")
            ],
            style={'width': '100%', 'height': '600px', 'marginTop': '20px'}
        ),
        # 標記左/右資訊
        html.Div("← LST 溫度", style={'position': 'absolute', 'top': '10px', 'left': '60px', 'zIndex': '1000', 'background': 'rgba(255,255,255,0.8)', 'padding': '5px', 'fontWeight': 'bold'}),
        html.Div("NDVI 植生 →", style={'position': 'absolute', 'top': '10px', 'right': '60px', 'zIndex': '1000', 'background': 'rgba(255,255,255,0.8)', 'padding': '5px', 'fontWeight': 'bold'}),
    ], style={'width': '80%', 'margin': '0 auto', 'position': 'relative', 'border': '2px solid #ddd'})
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
        return html.Div("該年度無可用資料"), f"年份: {selected_year} (無資料)"

    # 取得 EE Tile URL
    lst_url = lst_img.getMapId(LST_VIS)['tile_fetcher'].url_format
    ndvi_url = ndvi_img.getMapId(NDVI_VIS)['tile_fetcher'].url_format

    # 建立兩個 TileLayer，分別賦予特定的 ID
    # 這是關鍵：ID 必須與 SideBySide 中的 leftLayerId/rightLayerId 一致
    new_layers = [
        dl.TileLayer(url=lst_url, id="left-layer", opacity=1.0),
        dl.TileLayer(url=ndvi_url, id="right-layer", opacity=1.0)
    ]

    return new_layers, f"當前分析年份: {selected_year}"

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=7860, debug=False)