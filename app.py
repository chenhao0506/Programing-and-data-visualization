import os
import json
import ee
import geemap
import dash_leaflet as dl
import dash
from dash import dcc, html, Output, Input, State
from google.oauth2 import service_account

# ----------------------------------------------------
# 1. Hugging Face 環境變數 → Earth Engine 初始化
# ----------------------------------------------------
GEE_SECRET = os.environ.get("GEE_SERVICE_SECRET", "")
if not GEE_SECRET:
    raise ValueError("請先在 Hugging Face 設定環境變數 GEE_SERVICE_SECRET（完整 JSON）。")

service_account_info = json.loads(GEE_SECRET)

credentials = service_account.Credentials.from_service_account_info(
    service_account_info,
    scopes=["https://www.googleapis.com/auth/earthengine"]
)

ee.Initialize(credentials)
print("Earth Engine 初始化成功")

# ----------------------------------------------------
# 2. GEE 參數定義與去雲合成函數
# ----------------------------------------------------

# 台灣中部研究區
taiwan_region = ee.Geometry.Rectangle([120.24, 23.77, 120.69, 24.20])
# 台灣全島範圍
taiwan_composite_region = ee.Geometry.Rectangle([119.219433, 21.778681, 122.688102, 25.466353])
years = list(range(2015, 2026))

VIS_PARAMS = {
    'bands': ['SR_B4', 'SR_B3', 'SR_B2'], 
    'min': 0, 
    'max': 0.3,
    'gamma': 1.4
}

LST_VIS = {
    'min': 10,
    'max': 45,
    'palette': [
        '040274', '0502a3', '0502ce', '0602ff', '307ef3',
        '30c8e2', '3be285', '86e26f', 'b5e22e', 'ffd611',
        'ff8b13', 'ff0000', 'c21301', '911003'
    ]
}

def mask_clouds_and_scale(image):
    qa = image.select('QA_PIXEL')
    cloud_bit_mask = 1 << 3
    cloud_shadow_bit_mask = 1 << 4
    mask = qa.bitwiseAnd(cloud_bit_mask).eq(0).And(qa.bitwiseAnd(cloud_shadow_bit_mask).eq(0))
    return image.updateMask(mask).select(['SR_B4', 'SR_B3', 'SR_B2']).multiply(0.0000275).add(-0.2)

def get_l8_summer_composite(year):
    collection = (
        ee.ImageCollection("LANDSAT/LC08/C02/T1_L2")
        .filterBounds(taiwan_composite_region)
        .filterDate(f"{year}-06-01", f"{year}-08-31")
        .filter(ee.Filter.lt('CLOUD_COVER', 60))
    )
    if collection.size().getInfo() == 0:
        print(f"Warning: No Landsat 8 images found for Summer {year}.")
        return None
    final_image = collection.map(mask_clouds_and_scale).median().unmask(0)
    return final_image.clip(taiwan_composite_region)

def get_l8_summer_lst(year):
    collection = (
        ee.ImageCollection("LANDSAT/LC08/C02/T1_L2")
        .filterBounds(taiwan_region)
        .filterDate(f"{year}-06-01", f"{year}-08-31")
        .filter(ee.Filter.lt('CLOUD_COVER', 60))
    )
    if collection.size().getInfo() == 0:
        return None
    lst = (
        collection.select('ST_B10')
        .median()
        .multiply(0.00341802)
        .add(149.0)
        .subtract(273.15)
        .rename("LST_C")  # 改名為攝氏欄位
        .clip(taiwan_region)
    )
    return lst

# ----------------------------------------------------
# 3. Dash App 建立
# ----------------------------------------------------
app = dash.Dash(__name__)

center_lon = (119.219433 + 122.688102) / 2
center_lat = (21.778681 + 25.466353) / 2

app.layout = html.Div([
    html.H1("Landsat 8 夏季地表溫度 (LST) 互動分析", 
            style={'textAlign': 'center', 'margin-bottom': '20px', 'color': '#2C3E50'}),

    html.Div([
        html.H3(id='year-display', children=f"當前年份: {max(years)}", 
                style={'textAlign': 'center', 'color': '#34495E'}),
        dcc.Slider(
            id='year-slider',
            min=min(years),
            max=max(years),
            step=1,
            value=max(years),
            marks={str(y): {'label': str(y), 'style': {'color': '#16A085'}} for y in years},
            tooltip={"placement": "bottom", "always_visible": True}
        ),
    ], style={'padding': '20px', 'width': '80%', 'margin': '0 auto', 
              'background-color': '#ECF0F1', 'border-radius': '8px'}),
    
    html.Hr(style={'margin-top': '30px', 'margin-bottom': '30px'}),

    dcc.Loading(
        id="loading-map",
        type="circle",
        children=html.Div([
            dl.Map(
                id="leaflet-map",
                center=[center_lat, center_lon], 
                zoom=8,
                doubleClickZoom=False,
                style={'width': '100%', 'height': '500px', 'margin': '0 auto'},
                children=[
                    dl.TileLayer(url="https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png", 
                                 id='osm-layer', opacity=0.3),
                ]
            ),
            html.H3(id='lst-query-output', 
                    children='點擊地圖上的任意點位查詢地表溫度 (°C)...', 
                    style={'textAlign': 'center', 'margin-top': '20px', 
                           'color': '#C0392B', 'font-size': '20px'}),
            dcc.Store(id='map-click-data', data={})
        ], style={'width': '80%', 'margin': '0 auto', 'border': '5px solid #3498DB', 
                  'border-radius': '8px'})
    )
])

# ----------------------------------------------------
# 4. Callback：根據滑桿值更新影像
# ----------------------------------------------------
@app.callback(
    [Output('leaflet-map', 'children'),
     Output('year-display', 'children')],
    [Input('year-slider', 'value')],
    [State('leaflet-map', 'children')]
)
def update_map_layer(selected_year, current_children):
    print(f"Callback 1: 更新地圖圖層 for year: {selected_year}")
    status_text = f"當前年份: {selected_year} (LST 與底圖數據載入中...)"

    lst_image = get_l8_summer_lst(selected_year)
    composite_image = get_l8_summer_composite(selected_year)

    base_layers = [c for c in current_children if isinstance(c, dl.TileLayer) and c.id == 'osm-layer']
    gee_layers = []

    if composite_image is not None:
        try:
            map_info_comp = composite_image.getMapId(VIS_PARAMS, tileScale=8)
            tile_url_comp = map_info_comp['tile_fetcher'].url_format
            composite_layer = dl.TileLayer(
                url=tile_url_comp,
                id='gee-composite-layer',
                attribution=f'GEE Landsat 8 Composite Taiwan {selected_year}',
                opacity=1.0,
                zIndex=5
            )
            gee_layers.append(composite_layer)
            status_text = f"當前年份: {selected_year} (台灣全島底圖載入成功)"
        except ee.ee_exception.EEException as e:
            print(f"GEE Composite Tile Generation Error (Taiwan): {e}")
            status_text = f"當前年份: {selected_year} (台灣全島底圖載入失敗，原因：{e})"

    if lst_image is not None:
        try:
            map_info = lst_image.getMapId(LST_VIS)
            tile_url = map_info['tile_fetcher'].url_format
            lst_layer = dl.TileLayer(
                url=tile_url,
                id='gee-lst-layer',
                attribution=f'GEE Landsat 8 LST Taiwan Central {selected_year} / Data Clickable',
                opacity=0.8,
                zIndex=10
            )
            gee_layers.append(lst_layer)
            if "載入失敗" not in status_text:
                status_text = status_text.replace("載入成功", "及中部 LST 圖層載入成功")
            else:
                status_text = f"當前年份: {selected_year} (底圖失敗，但中部 LST 圖層載入成功)"
        except ee.ee_exception.EEException as e:
            print(f"GEE LST Tile Generation Error: {e}")
            status_text = f"當前年份: {selected_year} (LST 影像處理錯誤：{e})"
    else:
        print(f"Warning: No GEE LST images found for Summer {selected_year}.")
        if "載入成功" not in status_text:
            status_text = f"當前年份: {selected_year} (無可用 GEE 影像資料)"

    new_children = base_layers + gee_layers
    return new_children, status_text

# ----------------------------------------------------
# 5. Callback 2：處理單擊事件並查詢 LST
# ----------------------------------------------------
@app.callback(
    Output('lst-query-output', 'children'),
    [Input('leaflet-map', 'click_lat_lng')], 
    [State('year-slider', 'value')]
)
def query_lst_on_click(click_lat_lng, selected_year):
    if click_lat_lng is None:
        return '點擊地圖上的任意點位查詢地表溫度 (°C)...'

    lat, lng = click_lat_lng
    point_check = ee.Geometry.Point([lng, lat])
    if not taiwan_region.contains(point_check).getInfo():
        return html.Span([
            f'點擊座標 ({lat:.4f}, {lng:.4f})：',
            html.B('查詢失敗', style={'color': 'red'}),
            '，LST 數據僅限於台灣中部研究區域。'
        ])

    try:
        lst_image = get_l8_summer_lst(selected_year)
        if lst_image is None:
            return f'點擊座標 ({lat:.4f}, {lng:.4f})：抱歉，{selected_year} 年無 LST 影像資料。'

        lst_image_for_query = lst_image.unmask(-999)
        point = ee.Geometry.Point([lng, lat])
        point_data = lst_image_for_query.reduceRegion(
            reducer=ee.Reducer.first(),
            geometry=point,
            scale=30
        ).getInfo()

        lst_value = point_data.get('LST_C')
        if lst_value is None or abs(lst_value - (-999)) < 1:
            return html.Span([
                f'點擊座標 ({lat:.4f}, {lng:.4f})：',
                html.B('該點位無有效 LST 數值', style={'color': 'orange'}),
                ' (原為雲遮罩區或無數據區)'
            ])

        return html.Span([
            f'點擊座標 ({lat:.4f}, {lng:.4f})： ',
            html.B(f'地表溫度約 {lst_value:.2f} °C', style={'color': '#16A085'}),
            f' ({selected_year} 年夏季數據)'
        ])

    except ee.ee_exception.EEException as e:
        error_msg = str(e)
        print(f"GEE Reduce Region Error: {error_msg}")
        return html.Span([
            f'查詢錯誤 (GEE)：',
            html.B(error_msg, style={'color': 'red'})
        ])
    except Exception as e:
        error_msg = str(e)
        print(f"General Query Error: {error_msg}")
        return html.Span([
            f'查詢失敗 (程式錯誤)：',
            html.B(error_msg, style={'color': 'red'})
        ])

# ----------------------------------------------------
# 6. 啟動 Dash
# ----------------------------------------------------
if __name__ == "__main__":
    app.run(host="0.0.0.0", port=7860, debug=False)
