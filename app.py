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
# 2. GEE 參數定義與影像獲取函數 (Landsat 8/9, 30m 解析度)
# ----------------------------------------------------

# 移除 mask_l8_clouds 函數，因為我們將使用 CLOUD_COVER 篩選。

# 定義研究範圍與年份 (彰化縣的局部區域)
region = ee.Geometry.Rectangle([120.49, 23.92, 120.65, 24.10])
# 年份範圍調整為 2019 到 2025
years = list(range(2019, 2026)) 

# Landsat 8/9 L2 (Surface Reflectance) 可視化參數 (自然色)
# 波段名稱已修正為 L2 SR 集合使用的 'SR_B*' 格式。
VIS_PARAMS = {
    'bands': ['SR_B4', 'SR_B3', 'SR_B2'], 
    'min': 0, 
    'max': 0.3,
    'gamma': 1.4
}

def get_l8_july_image(year):
    """
    【修正邏輯】取得指定年份七月雲量百分比最低的 Landsat 8 影像。
    採用 .sort('CLOUD_COVER').first() 替代中位數聚合。
    """
    
    # 集合 ID 維持 'LANDSAT/LC08/C02/T1_L2' (Landsat 8 Collection 2 Level 2)
    collection = (
        ee.ImageCollection("LANDSAT/LC08/C02/T1_L2") 
        .filterBounds(region)
        .filterDate(f"{year}-07-01", f"{year}-07-31") 
        
        # 關鍵修正 1: 根據影像中繼資料中的 CLOUD_COVER 欄位排序，並選取雲量最低的第一張影像。
        .sort('CLOUD_COVER') 
    )
    
    # 檢查是否有足夠的影像
    size = collection.size().getInfo()
    if size == 0:
        print(f"Warning: No Landsat 8 images found for July {year}.")
        return None
    
    image = collection.first()
    
    # 檢查影像是否為空
    if image is None:
        print(f"Warning: Landsat 8 image is void for July {year}.")
        return None
    
    # 關鍵修正 2: 由於 Landsat L2 SR 影像使用整數 (DN 值)，
    # 為了與 VIS_PARAMS (min=0, max=0.3) 匹配，我們需要將 DN 值轉換為實際反射率 (0-1)。
    # 公式：Reflectance = (DN * 0.0000275) + (-0.2)
    def apply_scale_factors(img):
        # 僅選擇我們需要的 SR 波段
        optical_bands = img.select('SR_B4', 'SR_B3', 'SR_B2')
        # 應用縮放因子
        scaled_bands = optical_bands.multiply(0.0000275).add(-0.2)
        # 返回裁剪後的縮放影像
        return scaled_bands.clip(region).rename('SR_B4', 'SR_B3', 'SR_B2')

    # 應用縮放並裁剪
    final_image = apply_scale_factors(image)
    
    # 檢查最終影像是否有效
    try:
        # 檢查 SR_B4 波段是否存在，確認影像處理成功
        if final_image.select('SR_B4').bandNames().size().getInfo() == 0:
            print(f"Warning: Final image processing failed or band is missing for July {year}.")
            return None
    except Exception:
        print(f"Warning: GEE image processing failed for July {year}.")
        return None
        
    # 返回最終影像 (已縮放為 0-1 的反射率)
    return final_image.select('SR_B4', 'SR_B3', 'SR_B2')


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
    
    if image is not None:
        try:
            thumb_params = VIS_PARAMS.copy()
            
            # 解析度維持 60m (或可調整回 30m，但 60m 更穩定)
            thumb_params['scale'] = 60 
            thumb_params['region'] = region.getInfo()
            
            # 新增 CLOUD_COVER 資訊以顯示雲量百分比 (若影像非空)
            cloud_cover = image.get('CLOUD_COVER').getInfo()
            
            url = image.getThumbURL(thumb_params)

            status_text = f"當前年份: {selected_year} ( Landsat 8 載入成功，雲量: {cloud_cover:.2f}% )"
        except ee.ee_exception.EEException as e:
            # 捕獲 GEE 錯誤
            url = 'data:image/gif;base64,R0lGODlhAQABAIAAAAAAAP///yH5BAEAAAAALAAAAAABAAEAAAIBRAA7'
            status_text = f"當前年份: {selected_year} (GEE 影像處理錯誤：{e})"
        except Exception:
             # 捕獲其他錯誤，例如 CLOUD_COVER 無法取得
            url = 'data:image/gif;base64,R0lGODlhAQABAIAAAAAAAP///yH5BAEAAAAALAAAAAABAAEAAAIBRAA7'
            status_text = f"當前年份: {selected_year} (Landsat 8 載入成功，但無法取得雲量資訊)"
    else:
        # 如果找不到影像
        url = 'data:image/gif;base64,R0lGODlhAQABAIAAAAAAAP///yH5BAEAAAAALAAAAAABAAEAAAIBRAA7'
        status_text = f"當前年份: {selected_year} (錯誤：該時段無 Landsat 8 影像可用)"
        
    return url, status_text

# ----------------------------------------------------
# 5. Dash App 啟動 (使用你指定的格式)
# ----------------------------------------------------
if __name__ == '__main__':
    app.run(host="0.0.0.0", port=7860, debug=False)