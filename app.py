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
# 2. GEE 參數定義與影像獲取函數 (Landsat 8 LST 準備)
# ----------------------------------------------------

# Landsat 8 L2 TIRS (Band 10) 轉為亮度溫度的函數
def apply_landsat_temp_scale(image):
    """
    將 Landsat 8 C2 L2 的熱紅外線波段 (ST_B10) DN 值轉換為亮度溫度 (Kelvin, K)。
    需要使用元數據中的 K1 和 K2 參數。
    """
    # L2 集合中，Band 10 已經是經過大氣校正的地表溫度 (ST_B10)，
    # 但為了計算 LST，我們需要先獲得亮度溫度 (TB)。
    # 
    # 註：此處我們將直接使用 GEE 推薦的 LST 轉換函數，
    # 但由於 L2 集合已經有經過大氣校正的地表溫度 (ST_B10)，
    # 我們將先確保 ST_B10 數值被正確縮放。

    # C2 L2 集合的 ST_B10 波段，其縮放因子為 0.00341802 + 149.0
    # 但這個波段已經是 Kelvin 溫度，我們只需要縮放它。
    thermal = image.select('ST_B10')
    
    # L2 集合中，ST_B10 的縮放公式是：(DN * 0.00341802) + 149.0
    tb = thermal.multiply(0.00341802).add(149.0).rename('TB')
    
    # 確保 SR 波段也被正確縮放 (SR_B4, SR_B3, SR_B2)
    optical_bands = image.select('SR_B4', 'SR_B3', 'SR_B2')
    # SR 波段的縮放公式：(DN * 0.0000275) + (-0.2)
    scaled_optical = optical_bands.multiply(0.0000275).add(-0.2).rename('SR_B4', 'SR_B3', 'SR_B2')
    
    # 將縮放後的 TB 和 SR 波段合併
    return image.addBands(tb).addBands(scaled_optical)

# 定義研究範圍與年份 (彰化縣的局部區域)
region = ee.Geometry.Rectangle([120.49, 23.92, 120.65, 24.10])
# 年份範圍調整為 2019 到 2025
years = list(range(2019, 2026)) 

# Landsat 8/9 L2 LST 可視化參數 (攝氏度，從 20C 到 40C)
VIS_PARAMS = {
    # 顯示 LST 結果 (TB 是 LST 的預備步驟)
    'bands': ['TB'], 
    'min': 293.15, # 20 攝氏度 (273.15 + 20)
    'max': 313.15, # 40 攝氏度 (273.15 + 40)
    'palette': ['blue', 'cyan', 'green', 'yellow', 'red'] # 冷到熱
}

def get_l8_july_image(year):
    """
    取得指定年份七月雲量百分比最低的 Landsat 8 影像，並進行 TB 轉換。
    """
    
    collection = (
        ee.ImageCollection("LANDSAT/LC08/C02/T1_L2") 
        .filterBounds(region)
        .filterDate(f"{year}-01-01", f"{year}-07-31") 
        .sort('CLOUD_COVER') 
    )
    
    size = collection.size().getInfo()
    if size == 0:
        print(f"Warning: No Landsat 8 images found for {year} (Jan-Jul).")
        return None
    
    image = collection.first()
    
    if image is None:
        print(f"Warning: Landsat 8 image is void for {year} (Jan-Jul).")
        return None
    
    # 步驟 1: 應用 TB 和 SR 波段的縮放
    scaled_image = apply_landsat_temp_scale(image)

    # 步驟 2: 裁剪並將原始元數據複製到縮放後的影像上 (確保 CLOUD_COVER 存在)
    final_image = scaled_image.clip(region)
    final_image = final_image.set(image.toDictionary())
    
    # 檢查最終影像是否有效 (檢查 TB 波段)
    try:
        # 檢查 TB 波段是否存在
        if final_image.select('TB').bandNames().size().getInfo() == 0:
            print(f"Warning: Final image processing failed or TB band is missing for {year}.")
            return None
    except Exception:
        print(f"Warning: GEE image processing failed for {year}.")
        return None
        
    # 返回包含 TB 和 SR 波段的最終影像
    return final_image


# ----------------------------------------------------
# 3. Dash App 建立
# ----------------------------------------------------
app = dash.Dash(__name__)

app.layout = html.Div([
    html.H1("Landsat 8 LST 亮度溫度瀏覽器 (Kelvin) - GEE/Dash", 
             style={'textAlign': 'center', 'margin-bottom': '20px', 'color': '#2C3E50'}),
    
    # 滑桿控制區
    html.Div([
        html.H3(id='year-display', children=f"當前年份: {max(years)} (查詢 1月-7月)", 
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
    
    # 圖片顯示區
    dcc.Loading(
        id="loading-image",
        type="circle",
        children=html.Img(
            id='satellite-image', 
            style={
                'width': '80%', 
                'height': 'auto', 
                'display': 'block', 
                'margin': '0 auto', 
                'border': '5px solid #3498DB',
                'border-radius': '8px'
            }
        )
    )
])

# ----------------------------------------------------
# 4. Callback：根據滑桿值更新影像
# ----------------------------------------------------
@app.callback(
    [dash.Output('satellite-image', 'src'),
     dash.Output('year-display', 'children')],
    [dash.Input('year-slider', 'value')]
)
def update_image(selected_year):
    
    print(f"Callback triggered for year: {selected_year}")
    
    image = get_l8_july_image(selected_year)
    
    url = 'data:image/gif;base64,R0lGODlhAQABAIAAAAAAAP///yH5BAEAAAAALAAAAAABAAEAAAIBRAA7' 
    cloud_cover_display = "N/A (處理中)" 

    if image is not None:
        
        # 步驟 A: 嘗試獲取雲量和日期資訊
        try:
            cloud_cover = image.get('CLOUD_COVER').getInfo()
            date_info = ee.Date(image.get('system:time_start')).format('YYYY-MM-dd').getInfo()
            
            if cloud_cover is not None:
                cloud_cover_display = f"{cloud_cover:.2f}"
            else:
                cloud_cover_display = "N/A (值為空)"
        except Exception as e:
            print(f"Warning: Failed to retrieve CLOUD_COVER/Date for year {selected_year}. Error: {e}")
            date_info = "N/A"

        # 步驟 B: 嘗試生成縮圖 URL
        try:
            thumb_params = VIS_PARAMS.copy()
            thumb_params['scale'] = 60 # 調整回 60m 試試穩定性
            thumb_params['region'] = region.getInfo()
            
            # 使用 geemap.get_image_url 確保穩定性
            url = geemap.get_image_url(image.select('TB'), **thumb_params)

            # 狀態顯示提示當前顯示的是亮度溫度 (Kelvin)
            status_text = f"當前年份: {selected_year} (TB 載入日期: {date_info} | 雲量: {cloud_cover_display}%)"

        except ee.ee_exception.EEException as e:
            print(f"GEE Thumbnail Generation Error: {e}")
            status_text = f"當前年份: {selected_year} (GEE 影像處理錯誤：{e})"
        
        except Exception as e:
            print(f"General Thumbnail Error: {e}")
            status_text = f"當前年份: {selected_year} (載入成功，但縮圖 URL 產生失敗)"

    else:
        status_text = f"當前年份: {selected_year} (錯誤：該時段無 Landsat 8 影像可用 (1月-7月))"
        
    return url, status_text

# ----------------------------------------------------
# 5. Dash App 啟動 (使用你指定的格式)
# ----------------------------------------------------
if __name__ == '__main__':
    app.run(host="0.0.0.0", port=7860, debug=False)