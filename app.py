import os
import ee
import geemap
import json
import dash
from dash import dcc, html
import pandas as pd
from google.oauth2 import service_account

# ----------------------------------------------------
# 1. Earth Engine 初始化 (Hugging Face Secret 讀取)
# ----------------------------------------------------
print("--- 程式啟動與初始化 ---")
print(f"geemap version: {geemap.__version__}")

GEE_SERVICE_SECRET = os.environ.get("GEE_SERVICE_SECRET", "")
if not GEE_SERVICE_SECRET:
    raise ValueError(
        "請先在 Hugging Face 設定環境變數 GEE_SERVICE_SECRET，內容為完整的 JSON 字串"
    )

try:
    service_account_info = json.loads(GEE_SERVICE_SECRET)
except json.JSONDecodeError as e:
    raise ValueError(f"JSON 格式錯誤，請檢查 Secret 內容是否為有效的 JSON 字串: {e}")

# 建立 GEE Credentials
credentials = service_account.Credentials.from_service_account_info(
    service_account_info,
    scopes=["https://www.googleapis.com/auth/earthengine"]
)

ee.Initialize(credentials)
print("Earth Engine 初始化成功")

# ----------------------------------------------------
# 2. GEE 參數定義與 LST 影像獲取函數 (Landsat 8/9 L2)
# ----------------------------------------------------

# 定義研究範圍 (彰化縣的局部區域)
region = ee.Geometry.Rectangle([120.24, 23.77, 120.69, 24.20])
years = list(range(2019, 2026)) 

# LST 轉換函數
def get_lst_celsius_image(image):
    """將 Landsat L2 的 ST_B10 (LST) 波段從 Kelvin 轉換為 Celsius。"""
    
    # Landsat 8/9 L2 集合的 ST_B10 波段已經是 LST 產品 (Kelvin 溫度)
    # 縮放因子：乘 0.00341802，加 149.0
    
    lst_kelvin = image.select('ST_B10').multiply(0.00341802).add(149.0)
    
    # 轉換為攝氏度: T(°C) = T(K) - 273.15
    lst_celsius = lst_kelvin.subtract(273.15).rename('LST_C')
    
    # 複製原始影像的 metadata (確保 CLOUD_COVER 存在)
    lst_celsius = lst_celsius.set(image.toDictionary())
    
    return lst_celsius.clip(region)


def get_l8_lst_data(year, grid_size=0.01):
    """
    取得指定年份 6-8 月雲量最低的 Landsat 8/9 影像，並計算 LST 網格數據。
    
    Args:
        year (int): 年份
        grid_size (float): 網格大小（以度為單位，0.01度約1.1公里）
    
    Returns:
        dict: 包含 LST 網格數據 (DataFrame) 和中繼資料 (Cloud Cover)。
    """
    
    collection = (
        ee.ImageCollection("LANDSAT/LC08/C02/T1_L2") 
        .filterBounds(region)
        .filterDate(f"{year}-06-01", f"{year}-08-31") 
        .filterMetadata('CLOUD_COVER', 'less_than', 80) # 雲量上限過濾
        .sort('CLOUD_COVER') 
    )
    
    size = collection.size().getInfo()
    if size == 0:
        return {'status': f"No Landsat 8 images found for {year} (Cloud < 80%)"}
    
    image = collection.first()
    if image is None:
        return {'status': f"Landsat 8 image is void for {year}"}
    
    # 1. 計算 LST (°C) 影像
    lst_celsius_image = get_lst_celsius_image(image)
    cloud_cover = image.get('CLOUD_COVER').getInfo()
    
    # 2. 定義網格 (Regions)
    # 建立一個矩形網格，間隔為 grid_size 度
    grid_rects = []
    min_lon = region.bounds().getInfo()['coordinates'][0][0][0]
    min_lat = region.bounds().getInfo()['coordinates'][0][0][1]
    max_lon = region.bounds().getInfo()['coordinates'][0][2][0]
    max_lat = region.bounds().getInfo()['coordinates'][0][2][1]
    
    lon = min_lon
    while lon < max_lon:
        lat = min_lat
        while lat < max_lat:
            rect = ee.Geometry.Rectangle([lon, lat, lon + grid_size, lat + grid_size])
            grid_rects.append(rect)
            lat += grid_size
        lon += grid_size
        
    # 將網格轉換為 FeatureCollection
    grid_fc = ee.FeatureCollection(grid_rects)
    
    # 3. 提取每個網格的平均 LST 值 (使用 30m 解析度)
    mean_lst_data = lst_celsius_image.reduceRegions(
        collection=grid_fc,
        reducer=ee.Reducer.mean(),
        scale=30 # Landsat 8 LST 產品解析度
    )
    
    # 4. 將結果轉換為客戶端 DataFrame
    lst_list = mean_lst_data.getInfo()['features']
    
    # 處理數據，僅保留有效的 LST 值
    data = []
    for feature in lst_list:
        mean_val = feature['properties'].get('LST_C')
        if mean_val is not None:
            # 獲取網格的中心點坐標（用於繪圖參考）
            center = feature['geometry']['coordinates'][0][0] 
            data.append({
                'LST_C': mean_val,
                'lon_c': center[0],
                'lat_c': center[1]
            })

    df = pd.DataFrame(data)
    
    return {
        'status': 'success',
        'data': df,
        'cloud_cover': cloud_cover
    }

# ----------------------------------------------------
# 3. 視覺化輔助函數：將溫度轉換為顏色
# ----------------------------------------------------

def get_color(temp, min_temp, max_temp):
    """根據溫度值返回一個 HTML 顏色碼 (模擬分層設色圖)。"""
    
    # 簡單定義顏色範圍 (紅 -> 黃 -> 綠)
    if temp >= max_temp - 1: # 最高溫 (接近 max_temp)
        return '#dc3545' # 紅色
    elif temp >= (min_temp + max_temp) / 2: # 中高溫
        return '#ffc107' # 黃色
    elif temp >= min_temp + 1: # 中低溫
        return '#28a745' # 綠色
    else: # 最低溫 (接近 min_temp)
        return '#007bff' # 藍色

# ----------------------------------------------------
# 4. Dash App 建立
# ----------------------------------------------------

app = dash.Dash(__name__)

# 佈局修改：使用表格來呈現網格數據
app.layout = html.Div([
    html.H1("Landsat 8 地表溫度 (LST) 網格分析儀",              
             style={'textAlign': 'center', 'margin-bottom': '20px', 'color': '#2C3E50'}),
        
    # 滑桿控制區
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
    ], style={'padding': '20px', 'width': '80%', 'margin': '0 auto', 'background-color': '#ECF0F1', 'border-radius': '8px'}),
        
    html.Hr(style={'margin-top': '30px', 'margin-bottom': '30px'}),
    
    # 輸出區：顯示 LST 網格和中繼資料
    html.Div(id='data-output', style={'padding': '20px', 'width': '90%', 'margin': '0 auto'}),
])

# ----------------------------------------------------
# 5. Callback：根據滑桿值計算並顯示 LST 網格數據
# ----------------------------------------------------
@app.callback(
    [dash.Output('data-output', 'children'),
     dash.Output('year-display', 'children')],
    [dash.Input('year-slider', 'value')]
)
def display_lst_grid(selected_year):
    
    print(f"Callback triggered for year: {selected_year}")
    
    result = get_l8_lst_data(selected_year)
    
    if result['status'] != 'success':
        status_text = f"當前年份: {selected_year} (錯誤)"
        output_text = html.Div(result['status'], style={'color': 'red', 'fontSize': '20px'})
        return output_text, status_text

    df = result['data']
    cloud_cover = result['cloud_cover']
    
    # 計算全區域的溫度範圍，用於分層設色
    min_temp = df['LST_C'].min()
    max_temp = df['LST_C'].max()
    
    # 模擬您想要的表格網格輸出
    # 假設網格點的經度/緯度足以定義行和列
    df = df.sort_values(by=['lat_c', 'lon_c'], ascending=[False, True])
    
    # 確定表格結構 (基於 lon/lat 數量)
    lons = df['lon_c'].unique()
    lats = df['lat_c'].unique()
    
    # 創建表格內容
    table_rows = []
    
    # 根據緯度排序（從北到南）
    for lat in sorted(lats, reverse=True):
        row_data = []
        for lon in sorted(lons, reverse=False):
            cell = df[(df['lat_c'] == lat) & (df['lon_c'] == lon)]
            
            if not cell.empty:
                temp = cell['LST_C'].iloc[0]
                color = get_color(temp, min_temp, max_temp)
                
                cell_content = html.Div(
                    f"{temp:.1f}", 
                    style={
                        'backgroundColor': color,
                        'padding': '10px',
                        'margin': '2px',
                        'width': '60px',
                        'height': '30px',
                        'textAlign': 'center',
                        'color': 'white',
                        'fontWeight': 'bold',
                        'lineHeight': '10px'
                    },
                    # 模擬鼠標懸停效果 (使用 title 屬性)
                    title=f"LST: {temp:.2f}°C"
                )
            else:
                # 處理沒有數據的網格
                cell_content = html.Div('', style={'padding': '10px', 'margin': '2px', 'width': '60px', 'height': '30px', 'backgroundColor': '#f8f9fa'})
            
            row_data.append(cell_content)
            
        table_rows.append(html.Div(row_data, style={'display': 'flex', 'justifyContent': 'center'}))

    
    # 輸出結果
    status_text = f"當前年份: {selected_year} (Landsat 8/9 LST 計算完成，雲量: {cloud_cover:.2f}%)"
    
    output_content = html.Div([
        html.P(f" Landsat 8/9 地表平均溫度網格分析結果 ({selected_year} 年 6-8 月雲量最低影像):", style={'fontSize': '20px', 'fontWeight': 'bold', 'marginTop': '10px'}),
        html.P(f"全區域 LST 範圍: {min_temp:.2f}°C ~ {max_temp:.2f}°C", style={'fontSize': '16px', 'marginBottom': '20px'}),
        
        # LST 網格表格
        html.Div(table_rows, style={'width': 'fit-content', 'margin': '0 auto', 'border': '1px solid #ccc'}),
        
        # 圖例 (簡單版)
        html.Div([
            html.P("圖例:", style={'marginTop': '30px', 'fontWeight': 'bold'}),
            html.Div([
                html.Div("最高溫", style={'backgroundColor': '#dc3545', 'padding': '5px', 'marginRight': '10px', 'color': 'white'}),
                html.Div("中高溫", style={'backgroundColor': '#ffc107', 'padding': '5px', 'marginRight': '10px', 'color': 'black'}),
                html.Div("中低溫", style={'backgroundColor': '#28a745', 'padding': '5px', 'marginRight': '10px', 'color': 'white'}),
                html.Div("最低溫", style={'backgroundColor': '#007bff', 'padding': '5px', 'marginRight': '10px', 'color': 'white'}),
            ], style={'display': 'flex', 'justifyContent': 'center'}),
        ])
    ])
    
    return output_content, status_text

# ----------------------------------------------------
# 6. Dash App 啟動 (使用你指定的格式)
# ----------------------------------------------------
if __name__ == '__main__':
    # 優先使用環境變數 PORT，如果沒有則使用 7860 (Hugging Face 標準 Port)
    PORT = int(os.environ.get('PORT', 7860))
    app.run(host="0.0.0.0", port=PORT, debug=False)