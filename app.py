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
# 1. Earth Engine 初始化 (保持不變)
# ----------------------------------------------------
GEE_SECRET = os.environ.get("GEE_SERVICE_SECRET", "")
if not GEE_SECRET:
    # 這裡假設如果環境變數不存在，就使用本地默認的 GEE 身份驗證，如果失敗則程序會報錯。
    try:
        ee.Initialize()
        print("Earth Engine 初始化成功 (使用本地默認憑證)")
    except Exception as e:
        print(f"Earth Engine 初始化失敗: {e}")
else:
    service_account_info = json.loads(GEE_SECRET)
    credentials = service_account.Credentials.from_service_account_info(
        service_account_info,
        scopes=["https://www.googleapis.com/auth/earthengine"]
    )
    ee.Initialize(credentials)
    print("Earth Engine 初始化成功 (使用服務帳號憑證)")

# ----------------------------------------------------
# 2. GEE 參數定義與函數 (保持不變)
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
    'tileScale': 8
}

# 可視化參數 (LST) - 提取顏色、最小值和最大值
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
        image = collection.map(mask_clouds_and_scale).median()
        iterations = 10
        for i in range(iterations):
            image = image.unmask(image.focal_mean(radius=2, kernelType='circle', units='pixels'))
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
            .multiply(0.00341802) 
            .add(149.0)            
            .subtract(273.15)      
            .rename("LST_C")
            .clip(taiwan_region)
        )
        iterations = 10
        for i in range(iterations):
            lst = lst.unmask(lst.focal_mean(radius=10, kernelType='circle', units='pixels'))
        return lst
    except ee.ee_exception.EEException as e:
        print(f"GEE LST Error: {e}")
        return None
    except Exception as e:
        print(f"LST Error: {e}")
        return None

# ----------------------------------------------------
# 3. 圖例生成函數 (已修改，展示所有 14 個顏色)
# ----------------------------------------------------
def create_lst_legend(min_val: float, max_val: float, palette: List[str]) -> html.Div:
    """
    根據 LST 可視化參數創建 HTML 圖例，並展示調色盤中的所有顏色。
    """
    num_colors = len(palette) # 14 個顏色
    
    # 共有 num_colors - 1 (13) 個區間
    # 計算每一步長代表的溫度差異
    if num_colors > 1:
        temp_step = (max_val - min_val) / (num_colors - 1)
    else:
        temp_step = 0

    labels = []
    
    for i in range(num_colors):
        # 計算當前顏色塊代表的溫度下限
        temp_val = min_val + i * temp_step
        color = palette[i]
        
        # 標籤文字
        if i == num_colors - 1:
            # 最高溫標籤只顯示最高值
            label_text = f"≧ {temp_val:.1f} °C" 
        elif i == 0:
            # 最低溫標籤只顯示最低值
            label_text = f"≦ {temp_val:.1f} °C"
        else:
            # 中間標籤顯示當前顏色塊的溫度下限 (代表該顏色區間的起始點)
            label_text = f"{temp_val:.1f} °C" 

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
                html.Span(label_text, style={'fontSize': '12px', 'color': '#333'})
            ]
        )
        labels.append(label)

    # 反轉圖例，讓最低溫在底部 (較符合傳統圖例習慣，冷色在下，暖色在上)
    labels.reverse()
    
    # 為了讓標籤對應更直觀，我們將最高溫的標籤放在頂部，並將顏色塊的順序反轉以對應
    
    # 重新生成標籤，將溫度從上到下由熱到冷排列
    new_labels = []
    
    # 計算每塊顏色的溫度上限和下限
    for i in range(num_colors):
        # 當前顏色：從最熱 (i=0, palette[-1]) 到最冷 (i=13, palette[0])
        color = palette[num_colors - 1 - i] # 從 LST_PALETTE 的末尾取顏色 (最熱)
        
        # 溫度點：從 LST_MAX 遞減到 LST_MIN
        temp_at_point = max_val - i * temp_step
        
        if i == 0:
            # 第一個點：代表最高溫
            label_text = f"≧ {max_val:.1f} °C" 
        elif i == num_colors - 1:
            # 最後一個點：代表最低溫
            label_text = f"≦ {min_val:.1f} °C"
        else:
            # 中間點：代表該顏色區間的下限溫度
            lower_bound = max_val - (i+1) * temp_step
            upper_bound = max_val - i * temp_step
            # 顯示該顏色代表的溫度區間 (例如：39.2°C ~ 41.9°C)
            label_text = f"{upper_bound:.1f} ~ {lower_bound:.1f} °C"


        # 顏色塊和標籤
        label = html.Div(
            style={
                'display': 'flex',
                'alignItems': 'center',
                'marginBottom': '1px' # 縮小間距，以容納更多標籤
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
                html.Span(label_text, style={'fontSize': '10px', 'color': '#333'}) # 縮小字體
            ]
        )
        new_labels.append(label)

    # 調整圖例結構，讓它看起來像一個連續的色帶
    legend_elements = []
    
    # 頂部最高溫標籤
    legend_elements.append(
        html.Div(f"LST (Max: {LST_MAX:.1f} °C)", style={'textAlign': 'center', 'fontSize': '10px', 'color': '#333', 'marginBottom': '3px'})
    )
    
    # 連續的顏色帶 (14 個小色塊堆疊)
    color_band = []
    for i in range(num_colors):
        color = palette[num_colors - 1 - i] # 從熱到冷排列
        temp_point = max_val - i * temp_step
        
        # 標籤的位置，只顯示每隔幾個點的溫度值
        if i % 3 == 0 or i == num_colors - 1 or i == 0: # 顯示 0, 3, 6, 9, 12, 13 (6個標籤)
            temp_label = html.Span(
                f"{temp_point:.1f}", 
                style={'fontSize': '9px', 'position': 'absolute', 'left': '25px', 'top': f'{i * 10 - 5}px', 'color': '#333'}
            )
        else:
            temp_label = None

        color_band.append(
            html.Div(
                style={
                    'backgroundColor': f'#{color}',
                    'height': '10px', # 每個色塊的高度
                    'width': '15px'
                }
            )
        )
        if temp_label:
             color_band.append(temp_label)


    # 調整為色帶和標籤分開顯示的標準格式
    legend_items = []
    
    # 計算要顯示的標籤點
    num_points = 5 # 顯示 5 個標籤點
    
    for i in range(num_points):
        # 溫度值：從 LST_MAX 到 LST_MIN
        temp = LST_MAX - i * (LST_MAX - LST_MIN) / (num_points - 1)
        
        # 將溫度值對應到 0 到 1 的比例
        norm_temp = (temp - LST_MIN) / (LST_MAX - LST_MIN)
        
        # 確定標籤位置
        # 色帶長度約 14 * 10 = 140px，我們以百分比定位標籤
        
        legend_items.append(
            html.Div(
                f"{temp:.1f}°C",
                style={
                    'position': 'absolute',
                    'top': f'{i * 25}px', # 標籤垂直間距 (假設色帶長度 100px)
                    'right': '30px',
                    'fontSize': '11px',
                    'color': '#333'
                }
            )
        )


    final_legend_children = [
        html.H5("LST 地表溫度 (°C)", style={'textAlign': 'center', 'margin': '0 0 10px 0', 'fontSize': '14px', 'color': '#333'}),
        html.Div(
            style={
                'position': 'relative',
                'height': f'{num_colors * 15}px', # 總高度 (14 * 15 = 210px)
                'width': '60px',
                'margin': '0 auto'
            },
            children=[
                # 顏色帶
                html.Div(
                    style={
                        'display': 'flex',
                        'flexDirection': 'column',
                        'position': 'absolute',
                        'left': '0',
                        'top': '0',
                        'width': '20px',
                        'border': '1px solid #333'
                    },
                    children=[
                        html.Div(
                            style={
                                'backgroundColor': f'#{palette[num_colors - 1 - k]}', # 從熱到冷
                                'height': '15px', 
                                'width': '100%',
                            }
                        ) for k in range(num_colors)
                    ]
                ),
                # 標籤 (只顯示 5 個主要標籤)
                html.Div(
                    f"{LST_MAX:.1f}",
                    style={'position': 'absolute', 'top': '0px', 'left': '25px', 'fontSize': '11px', 'color': '#333'}
                ),
                html.Div(
                    f"{LST_MAX - 1 * (LST_MAX - LST_MIN) / 4:.1f}",
                    style={'position': 'absolute', 'top': f'{1 * (num_colors-1) * 15 / 4}px', 'left': '25px', 'fontSize': '11px', 'color': '#333'}
                ),
                html.Div(
                    f"{LST_MAX - 2 * (LST_MAX - LST_MIN) / 4:.1f}",
                    style={'position': 'absolute', 'top': f'{2 * (num_colors-1) * 15 / 4}px', 'left': '25px', 'fontSize': '11px', 'color': '#333'}
                ),
                html.Div(
                    f"{LST_MAX - 3 * (LST_MAX - LST_MIN) / 4:.1f}",
                    style={'position': 'absolute', 'top': f'{3 * (num_colors-1) * 15 / 4}px', 'left': '25px', 'fontSize': '11px', 'color': '#333'}
                ),
                html.Div(
                    f"{LST_MIN:.1f}",
                    style={'position': 'absolute', 'top': f'{(num_colors-1) * 15}px', 'left': '25px', 'fontSize': '11px', 'color': '#333'}
                ),
            ]
        )
    ]
    
    # 最終圖例容器
    return html.Div(
        id='lst-legend',
        style={
            'position': 'absolute',
            'top': '10px',        
            'right': '10px',
            'zIndex': 1000,       
            'backgroundColor': 'rgba(255, 255, 255, 0.9)',
            'padding': '10px',
            'borderRadius': '5px',
            'boxShadow': '0 2px 4px rgba(0,0,0,0.2)',
            'width': '120px', # 增加寬度以容納色帶和標籤
            'height': '250px' # 增加高度以容納色帶
        },
        children=final_legend_children
    )

# ----------------------------------------------------
# 4. Dash App 建立 (保持不變)
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
                    
                    # LST 圖例
                    lst_legend_component
                ]
            ),
            dcc.Store(id='map-click-data', data={})
        ], style={'width': '80%', 'margin': '0 auto', 'border': '5px solid #3498DB', 
                  'border-radius': '8px', 'position': 'relative'})
    )
])

# ----------------------------------------------------
# 5. Callback：根據滑桿值更新影像（保持不變）
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
        zIndex=1
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
                zIndex=5
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
                zIndex=10
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
            
    return new_children, status_text

# ----------------------------------------------------
# 6. 啟動 Dash
# ----------------------------------------------------
if __name__ == "__main__":
    app.run(host="0.0.0.0", port=7860, debug=False)