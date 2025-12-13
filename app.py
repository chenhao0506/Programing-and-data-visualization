import os
import json
import ee
import geemap
import dash_leaflet as dl
import dash
from dash import dcc, html, Output, Input, State
from google.oauth2 import service_account

# -----------------------------
# 1. 程式化創建 assets 資料夾與 CSS 檔案
#    現在直接針對 Leaflet 預設類別設置 z-index
# -----------------------------

ASSETS_DIR = "assets"
CSS_FILE_NAME = "custom.css"
CSS_FILE_PATH = os.path.join(ASSETS_DIR, CSS_FILE_NAME)

if not os.path.exists(ASSETS_DIR):
    os.makedirs(ASSETS_DIR)
    print(f"資料夾已創建: {ASSETS_DIR}")

# ********** 關鍵變更點 **********
# 直接針對 Leaflet 的預設圖層控制類 (.leaflet-control-layers) 設置 z-index
CSS_CONTENT = """
/* 確保 LayersControl 位於頂層，不會被其他元件遮擋 */
/* 適用於 dash_leaflet 1.1.3 版本，因其不支持 className */
.leaflet-control-layers {
    z-index: 999999 !important; 
}
"""

with open(CSS_FILE_PATH, "w", encoding="utf-8") as f:
    f.write(CSS_CONTENT)

print(f"CSS 檔案已創建或更新於: {CSS_FILE_PATH}")
print("---")

# ----------------------------------------------------
# 2. Hugging Face 環境變數 → Earth Engine 初始化
# ----------------------------------------------------
GEE_SECRET = os.environ.get("GEE_SERVICE_SECRET", "")
if not GEE_SECRET:
    raise ValueError("請先在 Hugging Face 設定環境變數 GEE_SERVICE_SECRET（完整 JSON）。")


service_account_info = json.loads(GEE_SECRET)

credentials = service_account.Credentials.from_service_account_info(
    service_account_info,
    scopes=["https://www.googleapis.com/auth/earthengine"]
)

ee.Initialize(credentials)
print("Earth Engine 初始化成功")



# ----------------------------------------------------
# 2. GEE 參數定義與去雲合成函數
# ----------------------------------------------------

# 定義研究範圍與年份
region = ee.Geometry.Rectangle([120.24, 23.77, 120.69, 24.20])
years = list(range(2015, 2026)) 

# 可視化參數 (維持不變)
VIS_PARAMS = {
    'bands': ['SR_B4', 'SR_B3', 'SR_B2'], 
    'min': 0, 
    'max': 0.3,
    'gamma': 1.4
}

LST_VIS = {
    'min': 10,
    'max': 45,
    'palette': [
    '040274', '0502a3', '0502ce', '0602ff', '307ef3',
    '30c8e2', '3be285', '86e26f', 'b5e22e', 'ffd611',
    'ff8b13', 'ff0000', 'c21301', '911003'
    ]}

def mask_clouds_and_scale(image):
    """
    對單張影像進行：
    1. 雲/雲影遮罩 (Masking)
    2. 數值縮放 (Scaling)
    """
    qa = image.select('QA_PIXEL')

    # Bit 3: Cloud, Bit 4: Cloud Shadow
    cloud_bit_mask = 1 << 3
    cloud_shadow_bit_mask = 1 << 4

    # 兩者皆為 0 才保留
    mask = qa.bitwiseAnd(cloud_bit_mask).eq(0) \
             .And(qa.bitwiseAnd(cloud_shadow_bit_mask).eq(0))

    # 應用遮罩並進行數值轉換 (DN -> Reflectance)
    # Reflectance = (DN * 0.0000275) - 0.2
    return image.updateMask(mask) \
                .select(['SR_B4', 'SR_B3', 'SR_B2']) \
                .multiply(0.0000275) \
                .add(-0.2)

def get_l8_summer_composite(year):
    """
    取得指定年份夏季 (6-8月) 的去雲合成影像。
    修正：使用 5 次迭代內插法填補空洞，並移除最後的強力填補，
    以確保數據真實性，避免過度平滑。
    """
    
    collection = (
        ee.ImageCollection("LANDSAT/LC08/C02/T1_L2") 
        .filterBounds(region)
        .filterDate(f"{year}-06-01", f"{year}-08-31") 
        .filter(ee.Filter.lt('CLOUD_COVER', 60)) 
    )
    
    # 檢查是否有影像
    size = collection.size().getInfo()
    if size == 0:
        print(f"Warning: No Landsat 8 images found for Summer {year}.")
        return None
    
    # 步驟 1: 原始去雲合成
    # 先算出這一季的中位數影像 (此時會有因為雲被挖掉產生的空洞)
    current_image = collection.map(mask_clouds_and_scale).median()
    
    # ---------------------------------------------------------
    # 步驟 2: 迭代內插填補 (Iterative Filling) - 5 次循環
    # ---------------------------------------------------------
    
    iterations = 10  
    
    for i in range(iterations):
        # 使用半徑 3 像素 (約90公尺) 進行取樣
        # 這樣能保證補進去的數值是參考很近的鄰居，比較準確
        filled = current_image.focal_mean(radius=3, kernelType='circle', units='pixels', iterations=1)

        final_fill = current_image.focal_mean(radius=100, kernelType='circle', units='pixels')
        final_image = current_image.unmask(final_fill)
        
        # 填補空洞：只補 NoData 的地方，原本有值的地方不動
        current_image = current_image.unmask(filled)
        

    # 裁剪到研究區域
    return current_image.clip(region)

def get_l8_summer_lst(year):
    """
    夏季 (6–8 月) 地表溫度合成圖（攝氏）
    """
    collection = (
        ee.ImageCollection("LANDSAT/LC08/C02/T1_L2")
        .filterBounds(region)
        .filterDate(f"{year}-06-01", f"{year}-08-31")
        .filter(ee.Filter.lt('CLOUD_COVER', 60))
    )

    if collection.size().getInfo() == 0:
        return None

    # ST_B10 → Kelvin → Celsius
    lst = (
        collection
        .select('ST_B10')
        .median()
        .multiply(0.00341802)
        .add(149.0)
        .subtract(273.15)
        .clip(region)
    )

    return lst

# ----------------------------------------------------
# 3. Dash App 建立
# ----------------------------------------------------
app = dash.Dash(__name__)

# 預設地圖中心點 (研究區域中心點)
center_lon = (120.24 + 120.69) / 2
center_lat = (23.77 + 24.20) / 2

app.layout = html.Div([
    html.H1("Landsat 8 夏季地表溫度 (LST) 互動分析", 
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

    # 地圖與查詢結果顯示區
    dcc.Loading(
        id="loading-map",
        type="circle",
        children=html.Div([
            # 地圖元件
            dl.Map(
                id="leaflet-map",
                center=[center_lat, center_lon], 
                zoom=10,
                doubleClickZoom=False, 
                style={'width': '100%', 'height': '500px', 'margin': '0 auto'},
                children=[
                    dl.TileLayer(url="https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png"),
                    # GEE 影像將通過 Callback 加入
                ]
            ),
            # 點擊查詢結果顯示區
            html.H3(id='lst-query-output', 
                    children='點擊地圖上的任意點位查詢地表溫度 (°C)...', 
                    style={'textAlign': 'center', 'margin-top': '20px', 'color': '#C0392B', 'font-size': '20px'}),
            
            # 儲存點擊經緯度的隱藏元件 (用於觸發 LST 查詢)
            dcc.Store(id='map-click-data', data={})
        ], style={'width': '80%', 'margin': '0 auto', 'border': '5px solid #3498DB', 'border-radius': '8px'})
    )
])


# ----------------------------------------------------
# 4. Callback：根據滑桿值更新影像 (修正後)
# ----------------------------------------------------
@app.callback(
    [dash.Output('satellite-image', 'src'),
     dash.Output('year-display', 'children')],
    [dash.Input('year-slider', 'value')]
)
def update_image(selected_year):
    
    print(f"Callback triggered for year: {selected_year}")
    
    # 預設透明圖
    url = 'data:image/gif;base64,R0lGODlhAQABAIAAAAAAAP///yH5BAEAAAAALAAAAAABAAEAAAIBRAA7' 
    status_text = f"當前年份: {selected_year} (處理中...)"

    # 1. 取得 RGB 合成影像
    rgb = get_l8_summer_composite(selected_year)
    
    # 2. 取得 LST 地表溫度影像
    lst = get_l8_summer_lst(selected_year)
    
    # 預設最終地圖為 None
    final_map = None

    if rgb is not None:
        # 如果 RGB 成功，將其設為底圖
        final_map = rgb.visualize(**VIS_PARAMS)
        status_text = f"當前年份: {selected_year}（夏季 RGB 合成）"

        if lst is not None:
            try:
                # --- 視覺化 LST ---
                lst_vis = {
                'min': 10,
                'max': 45,
                'palette': [
                '040274', '0502a3', '0502ce', '0602ff', '307ef3',
                '30c8e2', '3be285', '86e26f', 'b5e22e', 'ffd611',
                'ff8b13', 'ff0000', 'c21301', '911003'
                ]}

                lst_img = lst.visualize(**lst_vis)

                # --- RGB + LST 疊圖 ---
                # 讓 LST 疊在 RGB 上面
                final_map = ee.ImageCollection([
                    final_map, # RGB 放在第一層作為底圖
                    lst_img
                ]).mosaic() # 合成

                status_text = f"當前年份: {selected_year}（RGB + 地表溫度疊圖）"

            except ee.ee_exception.EEException as e:
                print(f"GEE LST Overlay Error: {e}")
                status_text = f"當前年份: {selected_year} (LST 處理錯誤，僅顯示 RGB)"
            
        
        # 3. 產生縮圖 URL
        try:
            url = final_map.getThumbURL({
                'scale': 100,
                'region': region.getInfo()
            })
        except Exception as e:
            print(f"General Thumbnail Error: {e}")
            status_text = f"當前年份: {selected_year} (縮圖產生失敗)"

    else:
        # 兩種影像都無法取得
        status_text = f"當前年份: {selected_year} (無 Landsat 影像資料)"
        
    return url, status_text

# ----------------------------------------------------
# 5. Callback 2：處理地圖點擊事件並查詢 LST 數值
# ----------------------------------------------------
@app.callback(
    Output('lst-query-output', 'children'),
    [Input('leaflet-map', 'dblclick_lat_lng')], 
    [State('year-slider', 'value')]
)
def query_lst_on_click(dblclick_lat_lng, selected_year):
    
    ctx = dash.callback_context
    if not ctx.triggered or dblclick_lat_lng is None:
        return '點擊地圖上的任意點位查詢地表溫度 (°C)...'
    
    lat, lng = dblclick_lat_lng
    
    try:
        # 1. 取得該年份的 LST 影像 (可能因年份無數據而回傳 None)
        lst_image = get_l8_summer_lst(selected_year)
        if lst_image is None:
            return f'點擊座標 ({lat:.4f}, {lng:.4f})：抱歉，{selected_year} 年無 LST 影像資料。'

        # 2. 穩健性處理：使用 unmask(-999) 避免無效數據點的錯誤
        lst_image_for_query = lst_image.unmask(-999) 

        # 3. 執行 GEE 查詢 (reduceRegion)
        point = ee.Geometry.Point([lng, lat])
        point_data = lst_image_for_query.reduceRegion( 
            reducer=ee.Reducer.first(), 
            geometry=point,
            scale=30
        ).getInfo()

        # 4. 提取 LST 數值
        lst_value = point_data.get('ST_B10')
        
        if lst_value is not None:
            lst_celsius = lst_value
            
            # 5. 判斷是否為無效數據 (-999)
            if abs(lst_celsius - (-999)) < 1:
                 return html.Span([
                     f'點擊座標 ({lat:.4f}, {lng:.4f})：',
                     html.B('該點位無有效 LST 數值', style={'color': 'orange'}),
                     ' (原為雲遮罩區或無數據區)'
                 ])

            # 6. 成功回傳結果
            return html.Span([
                f'點擊座標 ({lat:.4f}, {lng:.4f})： ',
                html.B(f'地表溫度約 {lst_celsius:.2f} °C', style={'color': '#16A085'}),
                f' ({selected_year} 年夏季數據)'
            ])
        else:
            # GEE 查詢結果為 None (雖然使用了 unmask，但仍可能發生)
            return f'點擊座標 ({lat:.4f}, {lng:.4f})：查詢失敗，結果為 None。'

    except ee.ee_exception.EEException as e:
        # 捕捉 GEE 服務端錯誤
        error_msg = str(e)
        print(f"GEE Reduce Region Error: {error_msg}")
        return html.Span([
            f'查詢錯誤 (GEE)：',
            html.B(error_msg, style={'color': 'red'})
        ])

    except Exception as e:
        # 捕捉 Python 程式碼錯誤
        error_msg = str(e)
        print(f"General Query Error: {error_msg}")
        return html.Span([
            f'查詢失敗 (程式錯誤)：',
            html.B(error_msg, style={'color': 'red'})
        ])
# ----------------------------------------------------
# 6. 啟動 Dash
# ----------------------------------------------------
if __name__ == "__main__":
    app.run(host="0.0.0.0", port=7860, debug=False)