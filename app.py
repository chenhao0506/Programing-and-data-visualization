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

# 優先使用環境變數 PORT，如果沒有則使用 7860 (Hugging Face 標準 Port)
PORT = int(os.environ.get('PORT', 7860))

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
# 2. GEE 穩定性輔助函數 (從 Streamlit 移植)
# ----------------------------------------------------

def applyScaleFactors(image):
    """應用 Landsat 8 L2 的光學和熱能縮放因子。"""
    # 光學波段 (SR_B*)：SR = DN * 0.0000275 - 0.2
    opticalBands = image.select('SR_B.').multiply(0.0000275).add(-0.2)
    # 熱能波段 (ST_B10)：LST (K) = DN * 0.00341802 + 149.0
    thermalBands = image.select('ST_B10').multiply(0.00341802).add(149.0).rename('LST_K')
    return image.addBands(opticalBands, overwrite=True).addBands(thermalBands, overwrite=True)

def cloudMask(image):
    """應用雲和雲陰影遮罩。"""
    cloud_shadow_bitmask = (1 << 3)
    cloud_bitmask = (1 << 5)
    qa = image.select('QA_PIXEL')
    mask = qa.bitwiseAnd(cloud_shadow_bitmask).eq(0).And(
                         qa.bitwiseAnd(cloud_bitmask).eq(0))
    # 更新遮罩，並保留 Landsat 的 metadata 屬性
    return image.updateMask(mask).copyProperties(image, ['CLOUD_COVER_LAND'])


# ----------------------------------------------------
# 3. LST 算法定義與數據提取
# ----------------------------------------------------

# 定義研究範圍 (彰化縣的局部區域)
region = ee.Geometry.Rectangle([120.24, 23.77, 120.69, 24.20])
years = list(range(2019, 2026)) 

# LST 算法參數定義
L8_LAMBDA = 10.9 # Landsat 8 TIRS Band 10 有效波長 (μm)
L8_RHO = 14388.0 / L8_LAMBDA 

def calculate_lst_celsius_image(image):
    """
    實作 LST 完整計算流程 (NDVI -> FV -> EM -> LST)。
    """
    # 0. 提取預處理後的波段 (B5/NIR, B4/Red, LST_K/T_b)
    nir = image.select('SR_B5') 
    red = image.select('SR_B4') 
    tb_kelvin = image.select('LST_K') # <-- Image 物件
    
    # --- 步驟 1: 計算 NDVI ---
    ndvi = nir.subtract(red).divide(nir.add(red)).rename('NDVI')
    
    # 假設 NDVI 範圍常數 (避免 reduceRegion 帶來的運算超時)
    NDVI_MAX = 0.8  
    NDVI_MIN = 0.05 
    
    # --- 步驟 2: 計算 Fractional Vegetation (FV) ---
    fv_numerator = ndvi.subtract(NDVI_MIN)
    fv_denominator = ee.Number(NDVI_MAX).subtract(NDVI_MIN)
    fv = fv_numerator.divide(fv_denominator).pow(2).rename("FV")
    fv = fv.where(fv.lt(0.0), 0.0).where(fv.gt(1.0), 1.0)
    
    # --- 步驟 3: 計算 Land Surface Emissivity (EM) ---
    em = fv.multiply(0.004).add(0.986).rename("EM")
    
    # --- 步驟 4: 計算 Land Surface Temperature (LST) ---
    # LST = T_b / (1 + (λ * T_b / ρ) * ln(ε))
    
    # 1. 計算 λ * T_b / ρ
    rho_const_numerator = tb_kelvin.multiply(L8_LAMBDA) # <-- 修正：使用 Image.multiply(Number)
    rho_const_factor = rho_const_numerator.divide(L8_RHO) 
    
    # 2. 乘以 ln(ε)
    rho_const = rho_const_factor.multiply(em.log())
    
    # 3. LST (Kelvin) = T_b / (1 + [rho_const])
    lst_kelvin = tb_kelvin.divide(ee.Number(1).add(rho_const))
    
    # 轉換為攝氏度: T(°C) = T(K) - 273.15
    lst_celsius = lst_kelvin.subtract(273.15).rename('LST_C')
    
    return lst_celsius.clip(region)


def get_l8_lst_data(year, grid_size=0.01):
    """
    使用 median 聚合的穩定 LST 數據獲取函數。
    """
    
    # 1. 建立 L8 集合並過濾
    collection = (
        ee.ImageCollection("LANDSAT/LC08/C02/T1_L2") 
        .filterBounds(region)
        .filterDate(f"{year}-06-01", f"{year}-08-31") 
    )
    
    # 2. 應用雲遮罩和縮放因子 (只保留雲量低的影像)
    filtered_collection = collection.filterMetadata('CLOUD_COVER_LAND', 'less_than', 80)
    
    size = filtered_collection.size().getInfo()
    if size == 0:
        return {'status': f"No Landsat 8 images found for {year} (Cloud < 80%)"}
    
    # 3. 處理集合: 縮放，雲遮罩，然後中位數聚合 (更穩定)
    processed_collection = filtered_collection.map(applyScaleFactors).map(cloudMask)
    image = processed_collection.median() # 中位數聚合
    
    # 4. 取得平均雲量 (用來顯示)
    mean_cloud_cover = filtered_collection.aggregate_mean('CLOUD_COVER_LAND').getInfo()
    
    # 5. 執行 LST 完整計算
    lst_celsius_image = calculate_lst_celsius_image(image)
    
    # 6. 定義網格並提取 LST 值
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
        
    grid_fc = ee.FeatureCollection(grid_rects)
    
    # 3. 提取每個網格的平均 LST 值 (30m 解析度)
    mean_lst_data = lst_celsius_image.reduceRegions(
        collection=grid_fc,
        reducer=ee.Reducer.mean(),
        geometry=region, 
        scale=30, 
        tileScale=8 
    )
    
    # 7. 將結果轉換為客戶端 DataFrame
    lst_list = mean_lst_data.getInfo()['features']
    
    data = []
    for feature in lst_list:
        mean_val = feature['properties'].get('LST_C')
        if mean_val is not None:
            bounds = feature['geometry']['coordinates'][0][0] 
            data.append({
                'LST_C': mean_val,
                'lon_c': bounds[0],
                'lat_c': bounds[1]
            })

    df = pd.DataFrame(data)
    
    return {
        'status': 'success',
        'data': df,
        'cloud_cover': mean_cloud_cover
    }

# ----------------------------------------------------
# 4. 視覺化輔助函數：將溫度轉換為顏色
# ----------------------------------------------------

def get_color(temp, min_temp, max_temp):
    """根據溫度值返回一個 HTML 顏色碼 (模擬分層設色圖)。"""
    
    temp_range = max_temp - min_temp
    
    if temp_range <= 0:
        return '#dc3545' 
        
    normalized_temp = (temp - min_temp) / temp_range
    
    # 根據標準化範圍賦予顏色 (四分位)
    if normalized_temp >= 0.75: 
        return '#dc3545' # 紅色 (最高溫)
    elif normalized_temp >= 0.5: 
        return '#ffc107' # 黃色 (中高溫)
    elif normalized_temp >= 0.25: 
        return '#28a745' # 綠色 (中低溫)
    else: 
        return '#007bff' # 藍色 (最低溫)

# ----------------------------------------------------
# 5. Dash App 建立
# ----------------------------------------------------

app = dash.Dash(__name__)

# 佈局修改：使用表格來呈現網格數據
app.layout = html.Div([
    html.H1("Landsat 8 LST 網格分層設色分析 (穩定 Median 算法)",              
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
    dcc.Loading(
        id="loading-grid",
        type="circle",
        children=html.Div(id='data-output', style={'padding': '20px', 'width': '90%', 'margin': '0 auto'})
    )
])

# ----------------------------------------------------
# 6. Callback：根據滑桿值計算並顯示 LST 網格數據
# ----------------------------------------------------
@app.callback(
    [dash.Output('data-output', 'children'),
     dash.Output('year-display', 'children')],
    [dash.Input('year-slider', 'value')]
)
def display_lst_grid(selected_year):
    
    print(f"Callback triggered for year: {selected_year}")
    
    # 呼叫 LST 數據獲取函數
    result = get_l8_lst_data(selected_year)
    
    if result['status'] != 'success':
        status_text = f"當前年份: {selected_year} (錯誤)"
        output_text = html.Div(result['status'], style={'color': 'red', 'fontSize': '20px'})
        return output_text, status_text

    df = result['data']
    cloud_cover = result['cloud_cover']
    
    if df.empty:
        status_text = f"當前年份: {selected_year} (無有效 LST 數據)"
        output_text = html.Div("無有效 LST 數據，請檢查雲量或區域。", style={'color': 'red', 'fontSize': '20px'})
        return output_text, status_text
        
    min_temp = df['LST_C'].min()
    max_temp = df['LST_C'].max()
    
    # 確定表格結構
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
                    # 模擬鼠標懸停效果 (title 屬性)
                    title=f"LST: {temp:.2f}°C"
                )
            else:
                cell_content = html.Div('', style={'padding': '10px', 'margin': '2px', 'width': '60px', 'height': '30px', 'backgroundColor': '#f8f9fa'})
            
            row_data.append(cell_content)
            
        table_rows.append(html.Div(row_data, style={'display': 'flex', 'justifyContent': 'center'}))

    
    # 輸出結果
    status_text = f"當前年份: {selected_year} (Landsat 8 LST 網格計算完成，平均雲量: {cloud_cover:.2f}%)"
    
    output_content = html.Div([
        html.P(f" Landsat 8 LST 網格分析結果 ({selected_year} 年 6-8 月):", style={'fontSize': '20px', 'fontWeight': 'bold', 'marginTop': '10px'}),
        html.P(f"全區域 LST 範圍: {min_temp:.2f}°C ~ {max_temp:.2f}°C", style={'fontSize': '16px', 'marginBottom': '20px'}),
        
        # LST 網格表格
        html.Div(table_rows, style={'width': 'fit-content', 'margin': '0 auto', 'border': '1px solid #ccc'}),
        
        # 圖例 (簡單版)
        html.Div([
            html.P("圖例 (基於全區域溫差的四分位):", style={'marginTop': '30px', 'fontWeight': 'bold'}),
            html.Div([
                html.Div("最高溫 (Q4)", style={'backgroundColor': '#dc3545', 'padding': '5px', 'marginRight': '10px', 'color': 'white'}),
                html.Div("中高溫 (Q3)", style={'backgroundColor': '#ffc107', 'padding': '5px', 'marginRight': '10px', 'color': 'black'}),
                html.Div("中低溫 (Q2)", style={'backgroundColor': '#28a745', 'padding': '5px', 'marginRight': '10px', 'color': 'white'}),
                html.Div("最低溫 (Q1)", style={'backgroundColor': '#007bff', 'padding': '5px', 'marginRight': '10px', 'color': 'white'}),
            ], style={'display': 'flex', 'justifyContent': 'center'}),
        ])
    ])
    
    return output_content, status_text

# ----------------------------------------------------
# 7. Dash App 啟動 (使用你指定的格式)
# ----------------------------------------------------
if __name__ == '__main__':
    PORT = int(os.environ.get('PORT', 7860))
    app.run(host="0.0.0.0", port=PORT, debug=False)