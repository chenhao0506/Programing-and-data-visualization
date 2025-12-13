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

# 研究區域（彰化縣）
region = ee.FeatureCollection("FAO/GAUL/2015/level2") \
    .filter(ee.Filter.eq("ADM2_NAME", "Changhua"))

# 雲遮罩
def mask_clouds(image):
    qa = image.select("QA_PIXEL")
    cloud = qa.bitwiseAnd(1 << 3).eq(0)
    shadow = qa.bitwiseAnd(1 << 4).eq(0)
    return image.updateMask(cloud.And(shadow))

# 夏季去雲合成影像
def get_l8_summer_composite(year):
    col = (
        ee.ImageCollection("LANDSAT/LC08/C02/T1_L2")
        .filterBounds(region)
        .filterDate(f"{year}-06-01", f"{year}-08-31")
        .filter(ee.Filter.lt("CLOUD_COVER", 60))
        .map(mask_clouds)
    )

    if col.size().getInfo() == 0:
        return None

    img = col.median()

    for _ in range(8):
        fill = img.focal_mean(radius=3, kernelType="circle", units="pixels")
        img = img.unmask(fill)

    return img.clip(region)

# True Color（百分位拉伸）
def get_l8_true_color(year):
    img = get_l8_summer_composite(year)
    if img is None:
        return None

    rgb = (
        img.select(["SR_B4", "SR_B3", "SR_B2"])
        .multiply(0.0000275)
        .add(-0.2)
    )

    # 百分位拉伸
    stats = rgb.reduceRegion(
        reducer=ee.Reducer.percentile([2, 98]),
        geometry=region,
        scale=30,
        maxPixels=1e13
    )

    vis = {
        "bands": ["SR_B4", "SR_B3", "SR_B2"],
        "min": [
            stats.get("SR_B4_p2"),
            stats.get("SR_B3_p2"),
            stats.get("SR_B2_p2"),
        ],
        "max": [
            stats.get("SR_B4_p98"),
            stats.get("SR_B3_p98"),
            stats.get("SR_B2_p98"),
        ],
    }

    return geemap.ee_tile_layer(rgb, vis, f"{year} True Color")

# LST
def get_l8_lst(year):
    img = get_l8_summer_composite(year)
    if img is None:
        return None

    lst = (
        img.select("ST_B10")
        .multiply(0.00341802)
        .add(149.0)
        .subtract(273.15)
    )

    vis = {
        "min": 20,
        "max": 38,
        "palette": [
            "#313695", "#4575b4", "#74add1", "#abd9e9",
            "#e0f3f8", "#ffffbf", "#fee090", "#fdae61",
            "#f46d43", "#d73027", "#a50026"
        ]
    }

    return geemap.ee_tile_layer(lst, vis, f"{year} LST")

def get_l8_lst_points(year):
    img = get_l8_summer_composite(year)
    if img is None:
        return None

    lst = (
        img.select("ST_B10")
        .multiply(0.00341802)
        .add(149.0)
        .subtract(273.15)
        .rename("LST")
    )

    # 在研究區內抽樣
    samples = lst.sample(
        region=region.geometry(),
        scale=1000,          
        geometries=True
    )

    return samples

# -----------------------------
# 3. Dash App
# -----------------------------
app = dash.Dash(__name__)

app.layout = html.Div([
    html.H3("彰化縣 夏季地表溫度與 True Color"),
    dl.Map(
        center=[24.05, 120.52],
        zoom=11,
        children=[
            dl.TileLayer(),
            dl.LayerGroup(id="map-layers"),
            dl.LayersControl(
                position="topright",
                # ********** 關鍵變更點：移除 className **********
            )
            ],
        style={"width": "100%", "height": "80vh"}
    )
])

@app.callback(
    Output("map-layers", "children"),
    Input("map-layers", "id")
)
def update_map(_):
    year = 2020
    layers = []

    # True Color
    tc = get_l8_true_color(year)
    if tc:
        # 設置為 BaseLayer
        layers.append(dl.BaseLayer(tc, name=tc.name, checked=True))

    # LST Raster
    lst = get_l8_lst(year)
    if lst:
        # 設置為 Overlay
        layers.append(dl.Overlay(lst, name=lst.name))

    # -------- hover 用 LST 點 --------
    pts = get_l8_lst_points(year)
    if pts:
        geojson = geemap.ee_to_geojson(pts)

        hover_layer = dl.GeoJSON(
            data=geojson,
            options=dict(
                pointToLayer="""
                function(feature, latlng) {
                    return L.circleMarker(latlng, {
                        radius: 4,
                        fillOpacity: 0,
                        opacity: 0
                    });
                }
                """,
                onEachFeature="""
                function(feature, layer) {
                    layer.bindTooltip(
                        "LST: " + feature.properties.LST.toFixed(1) + " °C",
                        {sticky: true}
                    );
                }
                """
            )
        )
        # 設置 Hover Layer 為 Overlay
        layers.append(dl.Overlay(hover_layer, name=f"{year} LST Hover Points", checked=True))

    return layers
# ----------------------------------------------------
# 6. 啟動 Dash
# ----------------------------------------------------
if __name__ == "__main__":
    app.run(host="0.0.0.0", port=7860, debug=False)