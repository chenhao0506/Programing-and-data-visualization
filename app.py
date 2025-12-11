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
# 2. GEE 參數定義與影像獲取函數
# ----------------------------------------------------

# 定義研究範圍與年份
region = ee.Geometry.Rectangle([120.48, 23.90, 120.65, 24.10])
years = list(range(2013, 2024))

# Landsat 8 C02 L2 (Surface Reflectance) 可視化參數 (自然色)
VIS_PARAMS = {
    'bands': ['SR_B4', 'SR_B3', 'SR_B2'], 
    'min': 0, 
    'max': 15000, # 維持優化後的對比度
    'gamma': 1.4
}

# 函數：取得七月 Landsat 8 影像集合的中位數影像 (回傳 ee.Image 物件)
def get_l8_july_image(year):
    """取得指定年份七月 Landsat 8 C02 L2 影像集合的中位數影像。"""
    collection = (
        ee.ImageCollection("LANDSAT/LC08/C02/T1_L2") 
        .filterBounds(region)
        # 維持七月日期範圍
        .filterDate(f"{year}-07-01", f"{year}-07-31") 
    )
    
    # 檢查集合是否為空
    if collection.size().getInfo() == 0:
        print(f"Warning: No Landsat 8 image found for July {year}.")
        return None
        
    # ** 修正點: 移除 .sort().limit(1)，改用 .median() 進行聚合去雲 **
    median_image = collection.median().clip(region) 
    
    # 選擇 RGB 波段並裁剪
    return median_image.select('SR_B4', 'SR_B3', 'SR_B2')


# ----------------------------------------------------
# 3. Dash App 建立 (Slider 移到圖片上方)
# ----------------------------------------------------
app = dash.Dash(__name__)

app.layout = html.Div([
    html.H1("Landsat 8 七月衛星影像瀏覽器 (Median) - GEE/Dash", style={'textAlign': 'center', 'margin-bottom': '20px'}),
    
    # ----------------------------------------------------
    # 滑桿控制區 (在上方)
    # ----------------------------------------------------
    html.Div([
        html.H3(id='year-display', children=f"當前年份: {max(years)}", style={'textAlign': 'center'}),
        
        dcc.Slider(
            id='year-slider',
            min=min(years),
            max=max(years),
            step=1,
            value=max(years),
            marks={str(y): {'label': str(y), 'style': {'color': '#77b0b1'}} for y in years},
            tooltip={"placement": "bottom", "always_visible": True}
        ),
    ], style={'padding': '20px', 'width': '80%', 'margin': '0 auto'}),
    
    html.Hr(),
    
    # ----------------------------------------------------
    # 圖片顯示區 (在下方)
    # ----------------------------------------------------
    dcc.Loading(
        id="loading-image",
        type="default",
        children=html.Img(
            id='satellite-image', 
            style={
                'width': '80%', 
                'height': 'auto', 
                'display': 'block', 
                'margin': '0 auto', 
                'border': '1px solid #ccc'
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
    """根據選擇的年份，獲取 Landsat 影像並生成 URL"""
    
    image = get_l8_july_image(selected_year)
    
    if image is not None:
        # 使用 getThumbURL 生成圖片網址
        url = image.getThumbURL({
            'params': VIS_PARAMS, 
            'scale': 500, # 維持你上次指定的 scale=500
            'region': region.getInfo()
        })
        status_text = f"當前年份: {selected_year} (影像載入成功 - 中位數聚合)"
    else:
        # 如果找不到影像，返回一個透明圖片的 Base64 數據，並提示
        url = 'data:image/gif;base64,R0lGODlhAQABAIAAAAAAAP///yH5BAEAAAAALAAAAAABAAEAAAIBRAA7'
        status_text = f"當前年份: {selected_year} (錯誤：該時段無可用影像)"
        
    return url, status_text

# ----------------------------------------------------
# 5. Dash App 啟動 (使用你指定的格式)
# ----------------------------------------------------
if __name__ == '__main__':
    print(f"--- Dash server starting on 0.0.0.0:{PORT} ---")
    app.run(host="0.0.0.0", port=PORT, debug=False)