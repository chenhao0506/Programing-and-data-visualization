import os
import ee
import geemap

# 驗證 geemap 是否匯入成功
print("geemap version:", geemap.__version__)

# 初始化 Earth Engine（Hugging Face 環境變數）
GEE_SERVICE_SECRET = os.environ.get("GEE_SERVICE_SECRET", "") 
credentials = service_account.Credentials.from_service_account_info(
    service_account_info,
    scopes=["https://www.googleapis.com/auth/earthengine"]
)

ee.Initialize(credentials)
print("Earth Engine 初始化成功")
