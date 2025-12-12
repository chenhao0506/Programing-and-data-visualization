import os
import json
import ee
import geemap
from google.oauth2 import service_account

import dash
from dash import html, dcc


# ----------------------------------------------------
# 1. Hugging Face 環境變數 → Earth Engine 初始化
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
# 2. 研究區域與年份設定
# ----------------------------------------------------
region = ee.Geometry.Rectangle([120.49, 23.92, 120.65, 24.10])
years = list(range(2019, 2026))  # 2019–2025


# ----------------------------------------------------
# 3. Landsat C2 L2 熱紅外 LST（ST_B10）提取函數
# ----------------------------------------------------
def get_l8_july_image(year):
    """
    取得指定年份 July (7/1–7/31) 雲量最低的 Landsat 8 L2 影像，
    並轉換 ST_B10 為 Kelvin 的地表溫度 LST。
    """

    collection = (
        ee.ImageCollection("LANDSAT/LC08/C02/T1_L2")
        .filterBounds(region)
        .filterDate(f"{year}-07-01", f"{year}-07-31")
        .sort("CLOUD_COVER")
    )

    image = collection.first()

    if image is None:
        print(f"No image for {year}")
        return None

    # ST_B10 轉 LST（Kelvin）
    lst = image.select("ST_B10") \
               .multiply(0.00341802) \
               .add(149.0) \
               .rename("LST")

    lst = lst.clip(region)
    lst = lst.copyProperties(image, image.propertyNames())

    return lst


# ----------------------------------------------------
# 4. LST 縮圖可視化參數 (Kelvin)
# ----------------------------------------------------
VIS = {
    "bands": ["LST"],
    "min": 293.15,   # 20°C
    "max": 313.15,   # 40°C
    "palette": ["blue", "cyan", "green", "yellow", "red"]
}


# ----------------------------------------------------
# 5. Dash App
# ----------------------------------------------------
app = dash.Dash(__name__)

app.layout = html.Div([
    html.H1("Landsat 8 LST — 七月最低雲量影像瀏覽器", 
            style={"text-align": "center"}),

    html.H3(id="year-display", 
            style={"text-align": "center"}),

    dcc.Slider(
        id="year-slider",
        min=min(years),
        max=max(years),
        step=1,
        value=max(years),
        marks={str(y): str(y) for y in years},
    ),

    html.Hr(),

    dcc.Loading(
        children=html.Img(
            id="satellite-image",
            style={"display": "block", "margin": "0 auto", "width": "80%"}
        )
    )
])


# ----------------------------------------------------
# 6. Callback：根據年份更新影像
# ----------------------------------------------------
@app.callback(
    [dash.Output("satellite-image", "src"),
     dash.Output("year-display", "children")],
    [dash.Input("year-slider", "value")]
)
def update_image(year):

    lst_image = get_l8_july_image(year)

    if lst_image is None:
        return (None, f"{year} 無影像可用")

    # 產生縮圖 URL
    try:
        url = geemap.get_image_url(lst_image, region=region, scale=90, **VIS)
    except Exception as e:
        print("Thumbnail error:", e)
        return (None, f"{year} 無法產生影像")

    return url, f"顯示年份：{year}"


# ----------------------------------------------------
# 7. 啟動 Dash
# ----------------------------------------------------
if __name__ == "__main__":
    app.run(host="0.0.0.0", port=7860, debug=False)