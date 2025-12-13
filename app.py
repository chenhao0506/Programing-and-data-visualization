import os
import json
import ee
import geemap
import dash_leaflet as dl
import dash
from dash import dcc, html, Output, Input, State
from google.oauth2 import service_account
from typing import List, Dict, Any

# ----------------------------------------------------
# 1. Hugging Face 環境變數 → Earth Engine 初始化
# ----------------------------------------------------
GEE_SECRET = os.environ.get("GEE_SERVICE_SECRET", "")
if not GEE_SECRET:
    # 這是為了在 Hugging Face 環境中運行，但在本地測試時可能需要手動註釋掉或設定
    # raise ValueError("請先在 Hugging Face 設定環境變數 GEE_SERVICE_SECRET（完整 JSON）。")
    print("Warning: GEE_SERVICE_SECRET environment variable not set. Assuming local setup or authentication failure.")
    # 如果本地測試，請確保您已使用 'earthengine authenticate' 進行身份驗證，或者直接使用您的服務帳號 JSON
    # 這裡假設如果環境變數不存在，就使用本地默認的 GEE 身份驗證，如果失敗則程序會報錯。
    try:
        ee.Initialize()
        print("Earth Engine 初始化成功 (使用本地默認憑證)")
    except Exception as e:
        print(f"Earth Engine 初始化失敗: {e}")
        # 如果無法初始化，後續的 GEE 相關函數將會失敗。
        # 為了讓 Dash App 結構能被看到，這裡不強制退出，但在實際運行時需要確保 GEE 初始化成功。
else:
    service_account_info = json.loads(GEE_SECRET)
    credentials = service_account.Credentials.from_service_account_info(
        service_account_info,
        scopes=["https://www.googleapis.com/auth/earthengine"]
    )
    ee.Initialize(credentials)
    print("Earth Engine 初始化成功 (使用服務帳號憑證)")

# ----------------------------------------------------
# 2. GEE 參數定義與去雲合成函數
# ----------------------------------------------------
# 台灣中部研究區
taiwan_region = ee.Geometry.Rectangle([120.24, 23.77, 120.69, 24.20])
# 台灣全島範圍
taiwan_composite_region = ee.Geometry.Rectangle([119.219433, 21.778681, 122.688102, 25.466353])
years = list(range(2015, 2026))

# 可視化參數 (底圖真彩)
VIS_PARAMS = {
    'bands': ['SR_B4', 'SR_B3', 'SR_B2'],
    'min': 0,
    'max': 0.3,
    'gamma': 1.4,
    'tileScale': 8  # 放到 vis_params
}

# 可視化參數 (LST) - 提取顏色、最小值和最大值，方便圖例使用
LST_MIN = 10
LST_MAX = 45
LST_PALETTE = [
    '040274', '0502a3', '0502ce', '0602ff', '307ef3',
    '30c8e2', '3be285', '86e26f', 'b5e22e', 'ffd611',
    'ff8b13', 'ff0000', 'c21301', '911003'
]
LST_VIS = {
    'min': LST_MIN,
    'max': LST_MAX,
    'palette': LST_PALETTE,
    'tileScale': 8
}

def mask_clouds_and_scale(image):
    qa = image.select('QA_PIXEL')
    cloud_bit_mask = 1 << 3
    cloud_shadow_bit_mask = 1 << 4
    mask = qa.bitwiseAnd(cloud_bit_mask).eq(0).And(qa.bitwiseAnd(cloud_shadow_bit_mask).eq(0))
    # 這裡僅選擇 RGB 波段進行去雲和縮放
    return image.updateMask(mask).select(['SR_B4', 'SR_B3', 'SR_B2']).multiply(0.0000275).add(-0.2)

def get_l8_summer_composite(year):
    try:
        collection = (
            ee.ImageCollection("LANDSAT/LC08/C02/T1_L2")
            .filterBounds(taiwan_composite_region)
            .filterDate(f"{year}-06-01", f"{year}-07-31")
            .filter(ee.Filter.lt('CLOUD_COVER', 60))
        )
        if collection.size().getInfo() == 0:
            print(f"Warning: No Landsat 8 images found for Summer {year} composite.")
            return None
        # 原始去雲合成
        image = collection.map(mask_clouds_and_scale).median()
        # --------------------------
        # 迭代內插填補空洞
        # --------------------------
        iterations = 10
        for i in range(iterations):
            # focal_mean 半徑 3 pixels，僅填補空值
            image = image.unmask(image.focal_mean(radius=3, kernelType='circle', units='pixels'))
        return image.clip(taiwan_composite_region)
    except ee.ee_exception.EEException as e:
        print(f"GEE Composite Error: {e}")
        return None
    except Exception as e:
        print(f"Composite Error: {e}")
        return None


def get_l8_summer_lst(year):
    try:
        collection = (
            ee.ImageCollection("LANDSAT/LC08/C02/T1_L2")
            .filterBounds(taiwan_region)
            .filterDate(f"{year}-06-01", f"{year}-08-31")
            .filter(ee.Filter.lt('CLOUD_COVER', 60))
        )
        if collection.size().getInfo() == 0:
            print(f"Warning: No Landsat 8 images found for Summer {year} LST.")
            return None
        lst = (
            collection.select('ST_B10')
            .median()
            .multiply(0.00341802) # 輻射亮度轉換為 K (開爾文)
            .add(149.0)            # 調整為 K (開爾文)
            .subtract(273.15)      # 轉換為 C (攝氏)
            .rename("LST_C")
            .clip(taiwan_region)
        )
        # --------------------------
        # 迭代內插填補空洞
        # --------------------------
        iterations = 10
        for i in range(iterations):
            lst = lst.unmask(lst.focal_mean(radius=3, kernelType='circle', units='pixels'))
        return lst
    except ee.ee_exception.EEException as e:
        print(f"GEE LST Error: {e}")
        return None
    except Exception as e:
        print(f"LST Error: {e}")
        return None

# ----------------------------------------------------
# 3. 圖例生成函數
# ----------------------------------------------------
def create_lst_legend(min_val: float, max_val: float, palette: List[str]) -> html.Div:
    """
    根據 LST 可視化參數創建 HTML 圖例。
    """
    # 決定圖例要顯示多少個標籤，例如分成 7 級
    num_labels = 7
    # 步長
    step = (max_val - min_val) / (num_labels - 1)
    
    # 準備標籤和對應的顏色索引 (從調色盤中均勻取樣)
    labels = []
    
    # 確保調色盤的顏色數量足夠
    num_colors = len(palette)
    
    for i in range(num_labels):
        # 計算溫度值
        temp = min_val + i * step
        # 確保顏色索引在範圍內
        color_index = int(i * (num_colors - 1) / (num_labels - 1))
        color = palette[color_index]
        
        # 顏色塊和標籤
        label = html.Div(
            style={
                'display': 'flex',
                'alignItems': 'center',
                'marginBottom': '3px'
            },
            children=[
                html.Div(
                    style={
                        'backgroundColor': f'#{color}',
                        'width': '15px',
                        'height': '15px',
                        'marginRight': '5px',
                        'border': '1px solid #333'
                    }
                ),
                html.Span(f"{temp:.1f} °C", style={'fontSize': '12px', 'color': '#333'})
            ]
        )
        labels.append(label)

    return html.Div(
        id='lst-legend',
        style={
            'position': 'absolute',
            'top': '10px',        # 調整圖例位置
            'right': '10px',
            'zIndex': 1000,       # 確保圖例在地圖圖層之上
            'backgroundColor': 'rgba(255, 255, 255, 0.9)',
            'padding': '10px',
            'borderRadius': '5px',
            'boxShadow': '0 2px 4px rgba(0,0,0,0.2)'
        },
        children=[
            html.H5("LST 地表溫度 (°C)", style={'textAlign': 'center', 'margin': '0 0 10px 0', 'fontSize': '14px', 'color': '#333'}),
            *labels  # 展開生成的標籤列表
        ]
    )

# ----------------------------------------------------
# 4. Dash App 建立
# ----------------------------------------------------
app = dash.Dash(__name__)
# 台灣中心點
center_lon = (119.219433 + 122.688102) / 2
center_lat = (21.778681 + 25.466353) / 2

# 生成圖例
lst_legend_component = create_lst_legend(LST_MIN, LST_MAX, LST_PALETTE)

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
                    # 預設的 OSM 底圖 (作為全球底圖)
                    dl.TileLayer(url="https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png",
                                  id='osm-layer', opacity=0.3, zIndex=1), 
                    
                    # 新增 LST 圖例，直接放在地圖 children 裡面
                    lst_legend_component
                ]
            ),
            dcc.Store(id='map-click-data', data={})
        ], style={'width': '80%', 'margin': '0 auto', 'border': '5px solid #3498DB', 
                  'border-radius': '8px', 'position': 'relative'}) # 設置 position: relative 讓圖例的 absolute 定位生效
    )
])

# ----------------------------------------------------
# 5. Callback：根據滑桿值更新影像（三層式）
# ----------------------------------------------------
@app.callback(
    [Output('leaflet-map', 'children'),
     Output('year-display', 'children')],
    [Input('year-slider', 'value')],
    [State('leaflet-map', 'children')])
def update_map_layer(selected_year, current_children):
    print(f"Callback 1: 更新地圖圖層 for year: {selected_year}")
    
    # 保留圖例元件
    legend_component = create_lst_legend(LST_MIN, LST_MAX, LST_PALETTE)
    
    status_text = f"當前年份: {selected_year} (LST 與底圖數據載入中...)"
    
    # 取得影像
    lst_image = get_l8_summer_lst(selected_year)
    composite_image = get_l8_summer_composite(selected_year)
    
    new_children = []
    
    # --- 0. 圖例元件 --- (最優先顯示)
    new_children.append(legend_component)
    
    # --- 最底層：全球 OSM ---
    global_osm = dl.TileLayer(
        url="https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png",
        id='global-osm-layer',
        opacity=1.0,
        zIndex=1 # 最底層
    )
    new_children.append(global_osm)
    
    # --- 中間層：台灣 Landsat 8 真彩影像 ---
    if composite_image is not None:
        try:
            map_info_comp = composite_image.getMapId(VIS_PARAMS)
            tile_url_comp = map_info_comp['tile_fetcher'].url_format
            landsat_layer = dl.TileLayer(
                url=tile_url_comp,
                id='gee-composite-layer',
                attribution=f'GEE Landsat 8 Composite Taiwan {selected_year}',
                opacity=1.0,
                zIndex=5 # 中間層
            )
            new_children.append(landsat_layer)
            status_text = f"當前年份: {selected_year} (台灣全島底圖載入成功)"
        except ee.ee_exception.EEException as e:
            print(f"GEE Composite Tile Generation Error (Taiwan): {e}")
            status_text = f"當前年份: {selected_year} (台灣全島底圖載入失敗，原因：{e})"
    
    # --- 最上層：彰化 LST ---
    if lst_image is not None:
        try:
            map_info = lst_image.getMapId(LST_VIS)
            tile_url = map_info['tile_fetcher'].url_format
            lst_layer = dl.TileLayer(
                url=tile_url,
                id='gee-lst-layer',
                attribution=f'GEE Landsat 8 LST Taiwan Central {selected_year}',
                opacity=0.8,
                zIndex=10 # 最上層
            )
            new_children.append(lst_layer)
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
            
    # 返回新的子元件列表，圖例會被保留
    return new_children, status_text

# ----------------------------------------------------
# 6. 啟動 Dash
# ----------------------------------------------------
if __name__ == "__main__":
    app.run(host="0.0.0.0", port=7860, debug=False)