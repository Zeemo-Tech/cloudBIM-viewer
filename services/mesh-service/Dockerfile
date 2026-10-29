FROM python:3.11-slim AS geometry-build
RUN apt-get update && apt-get install -y --no-install-recommends g++ \
    && rm -rf /var/lib/apt/lists/*
WORKDIR /native
COPY native/geometry.cpp native/build.sh ./
RUN sh build.sh

FROM python:3.11-slim

# 安装 Open3D 运行时依赖（OpenGL / EGL / OpenMP / X11）
RUN apt-get update && apt-get install -y --no-install-recommends \
    libgl1 \
    libegl1 \
    libglib2.0-0 \
    libgomp1 \
    libstdc++6 \
    libx11-6 \
    libxrender1 \
    libxext6 \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .
COPY --from=geometry-build /native/libgeometry.so /app/native/libgeometry.so

EXPOSE 8001

CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8001"]
