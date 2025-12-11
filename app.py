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
# 2. GEE 參數定義與影像獲取函數 (SENTINEL-2, 10m 解析度)
# ----------------------------------------------------

# 定義研究範圍與年份 (彰化縣的局部區域)
region = ee.Geometry.Rectangle([120.49, 23.92, 120.65, 24.10])
# 年份範圍調整為 2019 到 2024
years = list(range(2019, 2026)) 

# Sentinel-2 L2A (Surface Reflectance) 可視化參數 (自然色)
VIS_PARAMS = {
    'bands': ['B4', 'B3', 'B2'], # R, G, B 波段
    'min': 0, 
    'max': 3000, # S2 SR 範圍 0-10000，將 max 設為 3000 (0.3 反射率)
    'gamma': 1.4
}

def get_s2_july_image(year):
    """取得指定年份七月經過雲層遮蔽的 Sentinel-2 影像中位數 (10米解析度)。"""
    
    # *** 關鍵修正 1: 影像集合從 2019 年 7 月 1 日開始查詢到該年份的 7 月 31 日 ***
    # 由於您的需求是 medium/median，我們將只聚合該年份 7 月的影像
    collection = (
        ee.ImageCollection("COPERNICUS/S2_SR") 
        .filterBounds(region)
        .filterDate(f"{year}-07-01", f"{year}-07-31") 
        .filterMetadata('CLOUDY_PIXEL_PERCENTAGE', 'less_than', 95)
        
        # 應用 Sentinel-2 雲層遮蔽 (只遮蔽高概率雲 SCL=9, 10)
        .map(mask_s2_clouds)
    )
    
    # 檢查是否有足夠的影像
    size = collection.size().getInfo()
    if size == 0:
        print(f"Warning: No images found for July {year}.")
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
        
    # 裁剪並選擇 RGB 波段 (S2 波段名稱 B4/B3/B2)
    return image.clip(region).select('B4', 'B3', 'B2')


# ----------------------------------------------------
# 3. Dash App 建立
# ----------------------------------------------------
app = dash.Dash(__name__)

app.layout = html.Div([
    html.H1("Sentinel-2 七月衛星影像瀏覽器 (10m 解析度) - GEE/Dash", 
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
    
    image = get_s2_july_image(selected_year)
    
    if image is not None:
        try:
            # 傳遞 VIS_PARAMS 和其他參數
            thumb_params = VIS_PARAMS.copy()
            
            # 使用 20m/px 的 scale 避免檔案過大錯誤
            thumb_params['scale'] = 20 
            thumb_params['region'] = region.getInfo()
            
            url = image.getThumbURL(thumb_params)

            status_text = f"當前年份: {selected_year} (影像載入成功)"
        except ee.ee_exception.EEException as e:
            # 捕獲 GEE 錯誤
            url = 'data:image/gif;base64,R0lGODlhAQABAIAAAAAAAP///yH5BAEAAAAALAAAAAABAAEAAAIBRAA7'
            status_text = f"當前年份: {selected_year} (GEE 影像處理錯誤：{e})"
    else:
        # 如果找不到影像
        url = 'data:image/gif;base64,R0lGODlhAQABAIAAAAAAAP///yH5BAEAAAAALAAAAAABAAEAAAIBRAA7'
        status_text = f"當前年份: {selected_year} (錯誤：該時段無足夠高品質影像)"
        
    return url, status_text

# ----------------------------------------------------
# 5. Dash App 啟動 (使用你指定的格式)
# ----------------------------------------------------
if __name__ == '__main__':
    app.run(host="0.0.0.0", port=7860, debug=False)