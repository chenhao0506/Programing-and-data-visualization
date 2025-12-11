FROM python:3.11-slim

WORKDIR /app

# 安裝系統基本工具
RUN apt-get update && apt-get install -y \
    build-essential \
    && rm -rf /var/lib/apt/lists/*

# 複製需求檔
COPY requirements.txt .

# 安裝 Python 套件
RUN pip install --no-cache-dir -r requirements.txt

# 複製專案程式碼
COPY . .

# Hugging Face 預設 port 為 7860
EXPOSE 7860

CMD ["python", "app.py"]