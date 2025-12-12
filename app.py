import os
import ee
import geemap
import json
import dash
from dash import dcc, html
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
# 2. GEE 參數定義與紅外線能量獲取函數 (Landsat 8/9 TIRS Band 10)
# ----------------------------------------------------

# 定義研究範圍與年份 (彰化縣的局部區域)
# 研究區域保持不變
region = ee.Geometry.Rectangle([120.24, 23.77, 120.69, 24.20])
# 年份範圍調整為 2019 到 2025
years = list(range(2019, 2026)) 

# 移除 VIS_PARAMS，因為不再顯示影像

def get_l8_thermal_energy(year):
    """
    【修正功能】：取得指定年份 6-8 月雲量最低的 Landsat 8 影像，
    計算其 TIRS Band 10 的平均紅外線能量（亮度溫度）。
    """
    
    collection = (
        ee.ImageCollection("LANDSAT/LC08/C02/T1_L2") 
        .filterBounds(region)
        .filterDate(f"{year}-06-01", f"{year}-08-31") 
        .sort('CLOUD_COVER') 
    )
    
    size = collection.size().getInfo()
    if size == 0:
        print(f"Warning: No Landsat 8 images found for {year} (Jun-Aug).")
        return None
    
    # 原始影像 (包含 metadata 和 DN 值)
    image = collection.first()
    
    if image is None:
        print(f"Warning: Landsat 8 image is void for {year} (Jun-Aug).")
        return None
            
    # Landsat 8/9 L2 集合的 TIRS 波段是 Band 10 (BT)
    # Band 10 的值已經是 0.1 K 精度的亮度溫度 (TOA Brightness Temperature)
    # 轉換公式：TIRS (K) = (DN * 0.00341802) + 149.0
    
    # 步驟 1: 選擇 TIRS Band 10
    thermal_image = image.select('ST_B10')
    
    # 步驟 2: 應用溫度縮放因子 (將 DN 轉為 Kelvin 絕對溫度，即紅外線能量的量化)
    # L2 集合的 ST_B10 縮放因子為 0.00341802 和 149.0。
    # 註：此處使用的 ST_B10 是地表溫度 (LST) 產品，而非 TOA 亮度溫度，但它已是 LST 的標準起點。
    # 為了簡化，我們將使用 ST_B10 的縮放因子。
    LST_image = thermal_image.multiply(0.00341802).add(149.0)
    
    # 裁剪到研究區域
    LST_image = LST_image.clip(region)
    
    # 步驟 3: 計算研究區域的平均紅外線能量（LST Kelvin）
    # 使用 reduceRegion 獲取區域統計數據
    stats = LST_image.reduceRegion(
        reducer=ee.Reducer.mean(),
        geometry=region,
        scale=30, # Landsat 8 LST 產品解析度為 30m
        maxPixels=1e9
    )
    
    # 獲取平均 LST 值 (Kelvin)
    mean_lst_kelvin = stats.get('ST_B10').getInfo()
    
    # 獲取雲量資訊 (用於顯示)
    cloud_cover = image.get('CLOUD_COVER').getInfo()
    
    # 返回平均能量值和雲量
    return mean_lst_kelvin, cloud_cover


# ----------------------------------------------------
# 3. Dash App 建立
# ----------------------------------------------------
app = dash.Dash(__name__)

# 修改佈局：只顯示標題、滑桿和結果
app.layout = html.Div([
    html.H1("Landsat 8 地表紅外線能量（LST 平均值）計算 - GEE/Dash",              
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
    
    # 【新增】結果顯示區
    html.Div(id='energy-output', style={'textAlign': 'center', 'fontSize': '24px', 'padding': '20px'}),
])

# ----------------------------------------------------
# 4. Callback：根據滑桿值更新能量計算結果
# ----------------------------------------------------
@app.callback(
    [dash.Output('energy-output', 'children'),
     dash.Output('year-display', 'children')],
    [dash.Input('year-slider', 'value')]
)
def update_energy_output(selected_year):
    
    print(f"Callback triggered for year: {selected_year}")
    
    # 獲取能量值 (Kelvin) 和雲量
    result = get_l8_thermal_energy(selected_year)
    
    if result is None:
        # 如果找不到影像
        status_text = f"當前年份: {selected_year} (錯誤：該時段無 Landsat 8 影像可用)"
        output_text = html.Div("無足夠高品質影像進行計算", style={'color': 'red'})
        return output_text, status_text

    mean_lst_kelvin, cloud_cover = result
    
    try:
        if mean_lst_kelvin is None:
            raise ValueError("Mean LST Kelvin is None")

        # 步驟 1: 紅外線能量 (Kelvin) 顯示
        energy_kelvin = mean_lst_kelvin
        
        # 步驟 2 & 3: 轉換為攝氏度 (LST)
        lst_celsius = energy_kelvin - 273.15
        
        # 狀態顯示 (雲量)
        status_text = f"當前年份: {selected_year} (Landsat 8 載入成功，雲量: {cloud_cover:.2f}%)"
        
        # 結果格式化
        output_text = html.Div([
            html.P(f" Landsat 8 地表平均溫度計算結果 ({selected_year} 年 6-8 月雲量最低影像):", style={'fontSize': '20px', 'fontWeight': 'bold'}),
            html.P(f"區域平均紅外線能量 (亮度溫度): {energy_kelvin:.2f} K", style={'color': '#28A745'}),
            html.P(f"區域平均地表溫度 (LST): {lst_celsius:.2f} °C", style={'color': '#DC3545'}),
            html.P("請注意：此為 Landsat 8 L2 產品中 ST_B10 波段的平均值，已包含大氣校正。", style={'fontSize': '14px', 'marginTop': '10px'})
        ])
        
        return output_text, status_text

    except Exception as e:
        print(f"Calculation Error: {e}")
        status_text = f"當前年份: {selected_year} (計算失敗)"
        output_text = html.Div(f"計算過程發生錯誤: {e}", style={'color': 'red'})
        return output_text, status_text

# ----------------------------------------------------
# 5. Dash App 啟動 (使用你指定的格式)
# ----------------------------------------------------
if __name__ == '__main__':
    app.run(host="0.0.0.0", port=7860, debug=False)