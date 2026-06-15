FROM python:3.12-slim

WORKDIR /app

# 依赖先装 (利用层缓存)
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# 源码
COPY backend/ ./backend/
COPY frontend/ ./frontend/
COPY reports/ ./reports/
COPY config.example.toml ./config.example.toml

# SQLite 数据目录 (compose 挂卷覆盖)
RUN mkdir -p data

EXPOSE 8000

# config.toml 由 compose 挂载; 缺失则 load_config 回退默认 (足彩需配 proxy)
CMD ["python", "-m", "backend.run"]
