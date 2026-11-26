FROM python:3.10-slim

# ffmpeg 用于 RTMP 抓帧，必需
RUN apt-get update \
    && apt-get install -y --no-install-recommends ffmpeg \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt \
    || pip install --no-cache-dir -r requirements.txt -i https://mirrors.aliyun.com/pypi/simple/

COPY server.py utils.py index.html yolo11n.pt ./

ENV WC_HOST=0.0.0.0 \
    WC_PORT=1239 \
    PYTHONUNBUFFERED=1

EXPOSE 1239

HEALTHCHECK --interval=30s --timeout=5s --retries=3 \
    CMD python -c "import urllib.request;urllib.request.urlopen('http://127.0.0.1:1239/api/health',timeout=3)"

CMD ["python", "server.py"]
