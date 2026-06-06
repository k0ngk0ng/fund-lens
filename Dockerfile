FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    TZ=Asia/Shanghai

WORKDIR /app

RUN apt-get update \
    && apt-get install -y --no-install-recommends wget tzdata \
    && rm -rf /var/lib/apt/lists/*

# 国内网络默认用阿里云镜像（实测可用）；可用 --build-arg PIP_INDEX_URL=... 覆盖
# 走官方源： --build-arg PIP_INDEX_URL=https://pypi.org/simple
ARG PIP_INDEX_URL=https://mirrors.aliyun.com/pypi/simple/
COPY requirements.txt .
RUN pip install --index-url "${PIP_INDEX_URL}" --upgrade pip \
    && pip install --index-url "${PIP_INDEX_URL}" -r requirements.txt

COPY app ./app
COPY static ./static
COPY funds.yaml ./funds.yaml

EXPOSE 8000

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
