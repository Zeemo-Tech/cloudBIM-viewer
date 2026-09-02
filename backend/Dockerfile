FROM hub.macb.cc/library/golang:1.24 AS build
WORKDIR /src
COPY go.mod ./
RUN go mod download
COPY . .
RUN CGO_ENABLED=0 go build -o /out/cloudbim-backend .
RUN mkdir -p /out/data

FROM hub.macb.cc/library/debian:trixie-slim
WORKDIR /app
RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        ca-certificates \
        tzdata \
        libxml2 \
        libpcre2-8-0 \
        libglib2.0-0 \
        libgmp10 \
        libstdc++6 \
        libgcc-s1 \
    && rm -rf /var/lib/apt/lists/*
COPY --from=build /out/cloudbim-backend /app/cloudbim-backend
COPY --chown=65532:65532 --from=build /out/data /app/data
RUN groupadd -g 65532 appgroup \
    && useradd -u 65532 -g appgroup -M -s /usr/sbin/nologin appuser \
    && mkdir -p /opt/tools /app/data \
    && chown -R appuser:appgroup /app /opt/tools
ENV APP_ENV=production \
    ADDR=:8090 \
    CLOUDBIM_DATA_DIR=/app/data \
    PATH=/opt/tools/ifc_bundle:/opt/tools/gocesiumtiler:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin \
    LD_LIBRARY_PATH=/opt/tools/ifc_bundle \
    PROJ_LIB=/opt/tools/gocesiumtiler/share
VOLUME ["/app/data"]
EXPOSE 8090
USER appuser
ENTRYPOINT ["/app/cloudbim-backend"]
