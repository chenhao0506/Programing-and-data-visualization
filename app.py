import os
import ee
import geemap
import json
from google.oauth2 import service_account

# 驗證 geemap 是否匯入成功
print("geemap version:", geemap.__version__)

# 讀取 Hugging Face 環境變數
GEE_SERVICE_SECRET = os.environ.get("GEE_SERVICE_SECRET", "")
if not GEE_SERVICE_SECRET:
    raise ValueError("請先在 Hugging Face 設定環境變數 GEE_SERVICE_SECRET，內容為完整 JSON")

# 將 JSON 字串轉成 dict
service_account_info = json.loads(GEE_SERVICE_SECRET)

# 建立 Credentials
credentials = service_account.Credentials.from_service_account_info(
    service_account_info,
    scopes=["https://www.googleapis.com/auth/earthengine"]
)

# 初始化 Earth Engine
ee.Initialize(credentials)
print("Earth Engine 初始化成功")

# 測試 geemap Map 建立
m = geemap.Map(center=(23.97, 120.53), zoom=10)  # 中部台灣中心
print("Map 建立成功:", type(m))