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
# 2. GEE 參數定義與影像獲取函數 (Landsat 8, 30m 解析度)
# ----------------------------------------------------
# 

def mask_l8_clouds(image):
    """使用 Landsat 8 的 QA 波段 (pixel_qa)，遮蔽雲和雲陰影。"""
    # Landsat 8/9 Collection 2 Level-2 影像集合
    qa = image.select('QA_PIXEL')
    
    # 雲的位元遮罩：
    # Bit 3: 雲陰影 (Cloud Shadow)
    # Bit 5: 雲 (Cloud)
    cloud_shadow_bit = 1 << 3 # 二進位 00001000
    cloud_bit = 1 << 5        # 二進位 00100000
    
    # 建立遮罩：若 QA 位元不是雲或雲陰影，則為 True (保留)
    is_cloud_shadow = qa.bitwiseAnd(cloud_shadow_bit).eq(0)
    is_cloud = qa.bitwiseAnd(cloud_bit).eq(0)
    
    # 結合遮罩 (僅保留兩個條件都滿足的像素)
    mask = is_cloud_shadow.And(is_cloud)
    
    # Landsat 8 SR 影像需要 *0.0000275 + -0.2 才能轉換為反射率，
    # 但為了簡化，我們只應用遮罩，並在 VIS_PARAMS 中處理視覺化範圍。
    return image.updateMask(mask)
    

# 定義研究範圍與年份 (彰化縣的局部區域)
region = ee.Geometry.Rectangle([120.49, 23.92, 120.65, 24.10])
# 年份範圍調整為 2019 到 2025
years = list(range(2019, 2026)) 

# Landsat 8 L2 (Surface Reflectance) 可視化參數 (自然色)
VIS_PARAMS = {
    # Landsat 8/9 的自然色波段為 B4 (Red), B3 (Green), B2 (Blue)
    'bands': ['B4', 'B3', 'B2'], 
    'min': 5000, 
    'max': 20000, # L8 SR 範圍 0-65535，使用 5000-20000 (對應反射率 0.1375-0.55)
    'gamma': 1.4
}

def get_l8_july_image(year):
    """取得指定年份七月經過雲層遮蔽的 Landsat 8 影像中位數 (30米解析度)。"""
    
    # *** 關鍵修正 1: 影像集合更改為 Landsat 8 Collection 2 Tier 1 Surface Reflectance ***
    # Landsat 8 的 Collection ID
    collection = (
        ee.ImageCollection("LANDSAT/LC08/C02/T1_SR") 
        .filterBounds(region)
        .filterDate(f"{year}-07-01", f"{year}-07-31") 
        # L8/L9 不需要 CLOUDY_PIXEL_PERCENTAGE 過濾，因為 QA_PIXEL 更可靠
        
        # 應用 Landsat 8 雲層遮蔽 (使用 QA_PIXEL)
        .map(mask_l8_clouds)
    )
    
    # 檢查是否有足夠的影像
    size = collection.size().getInfo()
    if size == 0:
        print(f"Warning: No Landsat 8 images found for July {year}.")
        return None
    
    # *** 關鍵修正 2: 使用中位數聚合 (.median()) ***
    image = collection.median()
    
    # 檢查 median 聚合結果是否為全 NULL
    try:
        # 使用 select() 確保至少有 RGB 波段
        if image.select('B4').bandNames().size().getInfo() == 0:
            print(f"Warning: Median image contains no valid data for July {year}.")
            return None
    except Exception:
        print(f"Warning: GEE image processing failed for July {year}. Result is likely void.")
        return None
        
    # 裁剪並選擇 RGB 波段 (L8 波段名稱 B4/B3/B2)
    return image.clip(region).select('B4', 'B3', 'B2')


# ----------------------------------------------------
# 3. Dash App 建立 (此處無須修改)
# ----------------------------------------------------
app = dash.Dash(__name__)

app.layout = html.Div([
    html.H1("Landsat 8 七月衛星影像瀏覽器 (30m 解析度) - GEE/Dash", 
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
    
    # *** 關鍵修正 3: 呼叫 Landsat 8 的影像獲取函數 ***
    image = get_l8_july_image(selected_year)
    
    if image is not None:
        try:
            # 傳遞 VIS_PARAMS 和其他參數
            thumb_params = VIS_PARAMS.copy()
            
            # 使用 30m/px 的 scale (L8 原生解析度)
            thumb_params['scale'] = 30 
            thumb_params['region'] = region.getInfo()
            
            url = image.getThumbURL(thumb_params)

            status_text = f"當前年份: {selected_year} (Landsat 8 影像載入成功, 30m)"
        except ee.ee_exception.EEException as e:
            # 捕獲 GEE 錯誤
            url = 'data:image/gif;base64,R0lGODlhAQABAIAAAAAAAP///yH5BAEAAAAALAAAAAABAAEAAAIBRAA7'
            status_text = f"當前年份: {selected_year} (GEE 影像處理錯誤：{e})"
    else:
        # 如果找不到影像
        url = 'data:image/gif;base64,R0lGODlhAQABAIAAAAAAAP///yH5BAEAAAAALAAAAAABAAEAAAIBRAA7'
        status_text = f"當前年份: {selected_year} (錯誤：該時段無足夠高品質 Landsat 8 影像)"
        
    return url, status_text

# ----------------------------------------------------
# 5. Dash App 啟動 (使用你指定的格式)
# ----------------------------------------------------
if __name__ == '__main__':
    app.run(host="0.0.0.0", port=7860, debug=False)