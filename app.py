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
# 2. GEE 參數定義與影像獲取函數 (Landsat 8/9, 30m 解析度)
# ----------------------------------------------------

# 定義研究範圍與年份 (彰化縣的局部區域)
region = ee.Geometry.Rectangle([120.49, 23.92, 120.65, 24.10])
# 年份範圍調整為 2019 到 2025
years = list(range(2019, 2026)) 

# Landsat 8/9 L2 (Surface Reflectance) 可視化參數 (自然色)
VIS_PARAMS = {
    'bands': ['SR_B4', 'SR_B3', 'SR_B2'], 
    'min': 0, 
    'max': 0.3,
    'gamma': 1.4
}

def get_l8_july_image(year):
    """
    取得指定年份七月雲量百分比最低的 Landsat 8 影像。
    新增關鍵步驟：將原始影像的中繼資料複製到縮放後的影像上。
    """
    
    collection = (
        ee.ImageCollection("LANDSAT/LC08/C02/T1_L2") 
        .filterBounds(region)
        .filterDate(f"{year}-07-01", f"{year}-07-31") 
        .sort('CLOUD_COVER') 
    )
    
    size = collection.size().getInfo()
    if size == 0:
        print(f"Warning: No Landsat 8 images found for July {year}.")
        return None
    
    # 原始影像 (包含 metadata 和 DN 值)
    image = collection.first()
    
    if image is None:
        print(f"Warning: Landsat 8 image is void for July {year}.")
        return None
    
    
    # 步驟 1: 選擇 SR 波段並應用縮放因子 (DN -> 反射率)
    # Reflectance = (DN * 0.0000275) + (-0.2)
    final_image = (
        image.select('SR_B4', 'SR_B3', 'SR_B2')
        .multiply(0.0000275)
        .add(-0.2)
        # 裁剪操作在這裡執行
        .clip(region)
    )

    # 步驟 2: 【關鍵修正】將原始影像的元數據複製到縮放後的影像上
    # 這確保了 'CLOUD_COVER' 屬性會被保留。
    final_image = final_image.set(image.toDictionary())
    
    # 檢查最終影像是否有效
    try:
        if final_image.select('SR_B4').bandNames().size().getInfo() == 0:
            print(f"Warning: Final image processing failed or band is missing for July {year}.")
            return None
    except Exception:
        print(f"Warning: GEE image processing failed for July {year}.")
        return None
        
    return final_image


# ----------------------------------------------------
# 3. Dash App 建立
# ----------------------------------------------------
app = dash.Dash(__name__)

app.layout = html.Div([
    html.H1("Landsat 8 七月衛星影像瀏覽器 (雲量最低優先) - GEE/Dash", 
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
    
    image = get_l8_july_image(selected_year)
    
    url = 'data:image/gif;base64,R0lGODlhAQABAIAAAAAAAP///yH5BAEAAAAALAAAAAABAAEAAAIBRAA7' # 預設為透明圖
    cloud_cover_display = "N/A (中繼資料錯誤)" 

    if image is not None:
        
        # 步驟 A: 嘗試獲取雲量資訊 (現在應會成功)
        try:
            # 嘗試執行 GEE 遠端呼叫以獲取 CLOUD_COVER
            cloud_cover = image.get('CLOUD_COVER').getInfo()
            if cloud_cover is not None:
                cloud_cover_display = f"{cloud_cover:.2f}"
            else:
                cloud_cover_display = "N/A (值為空)"
        except Exception as e:
            # 捕獲所有獲取 metadata 時的錯誤 
            print(f"Warning: Failed to retrieve CLOUD_COVER for year {selected_year}. Error: {e}")
            # cloud_cover_display 維持預設值 "N/A (中繼資料錯誤)"

        # 步驟 B: 嘗試生成縮圖 URL
        try:
            thumb_params = VIS_PARAMS.copy()
            thumb_params['scale'] = 60 
            thumb_params['region'] = region.getInfo()
            
            # 生成縮圖 URL (這將觸發實際的影像處理)
            url = image.getThumbURL(thumb_params)

            status_text = f"當前年份: {selected_year} (Landsat 8 載入成功，雲量: {cloud_cover_display}%)"

        except ee.ee_exception.EEException as e:
            # 捕獲 GEE 影像處理錯誤 (e.g., 內存不足)
            print(f"GEE Thumbnail Generation Error: {e}")
            status_text = f"當前年份: {selected_year} (GEE 影像處理錯誤：{e})"
        
        except Exception as e:
            # 捕獲其他錯誤
            print(f"General Thumbnail Error: {e}")
            status_text = f"當前年份: {selected_year} (載入成功，但縮圖 URL 產生失敗)"

    else:
        # 如果找不到影像
        status_text = f"當前年份: {selected_year} (錯誤：該時段無 Landsat 8 影像可用)"
        
    return url, status_text

# ----------------------------------------------------
# 5. Dash App 啟動 (使用你指定的格式)
# ----------------------------------------------------
if __name__ == '__main__':
    app.run(host="0.0.0.0", port=7860, debug=False)