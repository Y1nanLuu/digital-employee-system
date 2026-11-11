# Debian 12 (bookworm) 固定基础镜像：python:3.11-slim 现指向 Debian 13 (trixie)，
# 其中 libglib2.0-0 已改名 libglib2.0-0t64，会导致本地 OCR 所需的系统库安装失败。
# 钉到 bookworm 既保证可构建，也满足候选版“依赖版本固定”要求。
FROM python:3.11-slim-bookworm

# 可选 apt 镜像：某些网络访问 deb.debian.org 会被拒，
# 构建时传 --build-arg APT_MIRROR=mirrors.aliyun.com 即可切换，默认保持官方源。
ARG APT_MIRROR=""
RUN if [ -n "$APT_MIRROR" ]; then \
      sed -i "s|deb.debian.org|$APT_MIRROR|g" /etc/apt/sources.list.d/debian.sources; \
    fi \
    && apt-get update \
    && apt-get install -y --no-install-recommends libgl1 libglib2.0-0 \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY app ./app
COPY finance ./finance
RUN mkdir -p /app/data
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
