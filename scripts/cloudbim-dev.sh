#!/usr/bin/env bash

set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
RUNTIME_DIR="$ROOT_DIR/.cloudbim"
FRONTEND_PID_FILE="$RUNTIME_DIR/frontend.pid"
REBAR_DEBUG_PID_FILE="$RUNTIME_DIR/rebar-debug.pid"
REBAR_DEBUG_PORT=5174
SCAN_BIM_PID_FILE="$RUNTIME_DIR/scan-bim-workbench.pid"
SCAN_BIM_LOG_FILE="$RUNTIME_DIR/scan-bim-workbench.log"
SCAN_BIM_PORT=8767
WORKBENCH_PID_FILE="$RUNTIME_DIR/pointcloud-workbench.pid"
WORKBENCH_LOG_FILE="$RUNTIME_DIR/pointcloud-workbench.log"

DB_PORT="${CLOUDBIM_DB_PORT:-15432}"
MESH_SERVICE_PORT="${CLOUDBIM_MESH_SERVICE_PORT:-8001}"
BACKEND_PORT="${CLOUDBIM_BACKEND_PORT:-8090}"
FRONTEND_HOST="${CLOUDBIM_FRONTEND_HOST:-0.0.0.0}"
FRONTEND_PORT="${CLOUDBIM_FRONTEND_PORT:-5173}"
WORKBENCH_ENABLED="${CLOUDBIM_POINTCLOUD_WORKBENCH_ENABLED:-false}"
WORKBENCH_HOST="${CLOUDBIM_POINTCLOUD_WORKBENCH_HOST:-0.0.0.0}"
WORKBENCH_PORT="${CLOUDBIM_POINTCLOUD_WORKBENCH_PORT:-8766}"
WORKBENCH_ALLOW_HOST="${CLOUDBIM_POINTCLOUD_WORKBENCH_ALLOW_HOST:-10.0.0.4}"
WORKBENCH_SOURCE="${CLOUDBIM_POINTCLOUD_WORKBENCH_SOURCE:-backend/data/assets/95b6b41c5857d9eb3407b155/source.las}"
WORKBENCH_OUTPUT="${CLOUDBIM_POINTCLOUD_WORKBENCH_OUTPUT:-backend/data/assets/95b6b41c5857d9eb3407b155/pointcloud-steps}"
WORKBENCH_PRIOR_CONFIG="${CLOUDBIM_POINTCLOUD_WORKBENCH_PRIOR_CONFIG:-.cloudbim/design-prior/config.json}"
WORKBENCH_WORKERS="${CLOUDBIM_POINTCLOUD_WORKBENCH_WORKERS:-16}"
WORKBENCH_THROUGH_STEP="${CLOUDBIM_POINTCLOUD_WORKBENCH_THROUGH_STEP:-6}"

# Keep bind-mounted development data accessible to the containerized backend,
# while allowing the mesh container to publish artifacts to the same group.
export CLOUDBIM_RUNTIME_UID="${CLOUDBIM_RUNTIME_UID:-$(id -u)}"
export CLOUDBIM_RUNTIME_GID="${CLOUDBIM_RUNTIME_GID:-$(id -g)}"

log() {
  printf '[cloudbim] %s\n' "$*"
}

fail() {
  printf '[cloudbim] error: %s\n' "$*" >&2
  exit 1
}

require_command() {
  command -v "$1" >/dev/null 2>&1 || fail "Missing required command: $1"
}

pid_is_running() {
  local pid_file="$1"
  [[ -f "$pid_file" ]] && kill -0 "$(<"$pid_file")" 2>/dev/null
}

stop_process() {
  local name="$1"
  local pid_file="$2"

  if ! pid_is_running "$pid_file"; then
    rm -f "$pid_file"
    return
  fi

  local pid
  pid="$(<"$pid_file")"
  log "Stopping $name (PID $pid)"
  kill "$pid"
  for _ in {1..20}; do
    kill -0 "$pid" 2>/dev/null || break
    sleep 0.25
  done
  kill -0 "$pid" 2>/dev/null && kill -9 "$pid" || true
  rm -f "$pid_file"
}

port_is_available() {
  local port="$1"
  ! lsof -nP -iTCP:"$port" -sTCP:LISTEN >/dev/null 2>&1
}

set_env_value() {
  local file="$1"
  local key="$2"
  local value="$3"
  sed -i "s|^${key}=.*|${key}=${value}|" "$file"
}

read_env_value() {
  local file="$1"
  local key="$2"
  local fallback="$3"
  local value
  value="$(sed -n "s|^${key}=||p" "$file" | tail -n 1)"
  printf '%s\n' "${value:-$fallback}"
}

ensure_env_files() {
  if [[ ! -f "$ROOT_DIR/.env" ]]; then
    cp "$ROOT_DIR/.env.example" "$ROOT_DIR/.env"
    set_env_value "$ROOT_DIR/.env" DB_PORT "$DB_PORT"
    set_env_value "$ROOT_DIR/.env" MESH_SERVICE_PORT "$MESH_SERVICE_PORT"
    log "Created .env with database port $DB_PORT and mesh-service port $MESH_SERVICE_PORT"
  fi

}

load_ports() {
  DB_PORT="$(read_env_value "$ROOT_DIR/.env" DB_PORT "$DB_PORT")"
  MESH_SERVICE_PORT="$(read_env_value "$ROOT_DIR/.env" MESH_SERVICE_PORT "$MESH_SERVICE_PORT")"
  BACKEND_PORT="$(read_env_value "$ROOT_DIR/.env" BACKEND_PORT "$BACKEND_PORT")"
  FRONTEND_HOST="$(read_env_value "$ROOT_DIR/.env" FRONTEND_HOST "$FRONTEND_HOST")"
  FRONTEND_PORT="$(read_env_value "$ROOT_DIR/.env" FRONTEND_PORT "$FRONTEND_PORT")"
  WORKBENCH_ENABLED="$(read_env_value "$ROOT_DIR/.env" POINTCLOUD_WORKBENCH_ENABLED "$WORKBENCH_ENABLED")"
  WORKBENCH_HOST="$(read_env_value "$ROOT_DIR/.env" POINTCLOUD_WORKBENCH_HOST "$WORKBENCH_HOST")"
  WORKBENCH_PORT="$(read_env_value "$ROOT_DIR/.env" POINTCLOUD_WORKBENCH_PORT "$WORKBENCH_PORT")"
  WORKBENCH_ALLOW_HOST="$(read_env_value "$ROOT_DIR/.env" POINTCLOUD_WORKBENCH_ALLOW_HOST "$WORKBENCH_ALLOW_HOST")"
  WORKBENCH_SOURCE="$(read_env_value "$ROOT_DIR/.env" POINTCLOUD_WORKBENCH_SOURCE "$WORKBENCH_SOURCE")"
  WORKBENCH_OUTPUT="$(read_env_value "$ROOT_DIR/.env" POINTCLOUD_WORKBENCH_OUTPUT "$WORKBENCH_OUTPUT")"
  WORKBENCH_PRIOR_CONFIG="$(read_env_value "$ROOT_DIR/.env" POINTCLOUD_WORKBENCH_PRIOR_CONFIG "$WORKBENCH_PRIOR_CONFIG")"
  WORKBENCH_WORKERS="$(read_env_value "$ROOT_DIR/.env" POINTCLOUD_WORKBENCH_WORKERS "$WORKBENCH_WORKERS")"
  WORKBENCH_THROUGH_STEP="$(read_env_value "$ROOT_DIR/.env" POINTCLOUD_WORKBENCH_THROUGH_STEP "$WORKBENCH_THROUGH_STEP")"
}

resolve_repo_path() {
  local path="$1"
  if [[ "$path" = /* ]]; then
    printf '%s\n' "$path"
  else
    printf '%s/%s\n' "$ROOT_DIR" "$path"
  fi
}

workbench_is_enabled() {
  [[ "$WORKBENCH_ENABLED" == "true" || "$WORKBENCH_ENABLED" == "1" || "$WORKBENCH_ENABLED" == "yes" ]]
}

wait_for_postgres() {
  log "Waiting for PostgreSQL"
  for _ in {1..30}; do
    if docker compose -f "$ROOT_DIR/docker-compose.yml" exec -T postgres \
      sh -c 'pg_isready -U "$POSTGRES_USER" -d "$POSTGRES_DB"' >/dev/null 2>&1; then
      return
    fi
    sleep 1
  done
  fail "PostgreSQL did not become ready; inspect with '$0 logs'"
}

wait_for_mesh_service() {
  log "Waiting for mesh service"
  for _ in {1..30}; do
    if curl -fsS "http://127.0.0.1:$MESH_SERVICE_PORT/algorithms" >/dev/null 2>&1; then
      return
    fi
    sleep 1
  done
  fail "Mesh service did not become ready; inspect with '$0 logs'"
}

wait_for_backend() {
  log "Waiting for backend"
  for _ in {1..30}; do
    if curl -fsS "http://127.0.0.1:$BACKEND_PORT/health" >/dev/null 2>&1; then
      return
    fi
    sleep 1
  done
  fail "Backend did not become ready; inspect $RUNTIME_DIR/backend.log"
}

wait_for_workbench() {
  log "Waiting for point-cloud workbench"
  for _ in {1..30}; do
    if curl -fsS "http://127.0.0.1:$WORKBENCH_PORT/api/status" >/dev/null 2>&1; then
      return
    fi
    sleep 1
  done
  fail "Point-cloud workbench did not become ready; inspect $WORKBENCH_LOG_FILE"
}

start_workbench() {
  workbench_is_enabled || return

  local python="$RUNTIME_DIR/mesh-venv/bin/python"
  local source output prior_config
  source="$(resolve_repo_path "$WORKBENCH_SOURCE")"
  output="$(resolve_repo_path "$WORKBENCH_OUTPUT")"
  prior_config="$(resolve_repo_path "$WORKBENCH_PRIOR_CONFIG")"

  [[ -x "$python" ]] || fail "Point-cloud workbench requires $python"
  [[ -f "$source" ]] || fail "Point-cloud workbench source not found: $source"
  [[ -f "$prior_config" ]] || fail "Point-cloud workbench prior config not found: $prior_config"

  if ! pid_is_running "$WORKBENCH_PID_FILE" && ! port_is_available "$WORKBENCH_PORT"; then
    fail "Point-cloud workbench port $WORKBENCH_PORT is already in use by an unmanaged process"
  fi

  if ! pid_is_running "$WORKBENCH_PID_FILE"; then
    log "Starting supervised point-cloud workbench"
    setsid "$ROOT_DIR/scripts/cloudbim-supervise.sh" \
      "$python" "$ROOT_DIR/scripts/pointcloud-debug.py" \
      --source "$source" --output "$output" \
      --host "$WORKBENCH_HOST" --port "$WORKBENCH_PORT" --allow-host "$WORKBENCH_ALLOW_HOST" \
      --workers "$WORKBENCH_WORKERS" --through-step "$WORKBENCH_THROUGH_STEP" \
      --prior-config "$prior_config" \
      >"$WORKBENCH_LOG_FILE" 2>&1 < /dev/null &
    echo $! >"$WORKBENCH_PID_FILE"
  fi

  wait_for_workbench
}

start() {
  require_command docker
  require_command npm
  require_command curl
  require_command lsof
  # GNU coreutils `setsid` is not installed by default on macOS. The
  # frontend does not require a separate session there, so fall back to
  # `nohup` when setsid is unavailable.

  mkdir -p "$RUNTIME_DIR"
  ensure_env_files
  load_ports

  if ! pid_is_running "$FRONTEND_PID_FILE" && ! port_is_available "$FRONTEND_PORT"; then
    fail "Port $FRONTEND_PORT is already in use"
  fi

  log "Starting PostgreSQL, mesh service, and backend"
  docker compose -f "$ROOT_DIR/docker-compose.yml" up -d --build postgres mesh-service backend
  wait_for_postgres
  wait_for_mesh_service

  if [[ ! -d "$ROOT_DIR/node_modules" ]]; then
    log "Installing frontend dependencies"
    (cd "$ROOT_DIR" && npm ci)
  fi

  wait_for_backend

  if ! pid_is_running "$FRONTEND_PID_FILE"; then
    log "Starting frontend"
    if command -v setsid >/dev/null 2>&1; then
      setsid env "VITE_API_PROXY_TARGET=http://127.0.0.1:$BACKEND_PORT" \
        "$ROOT_DIR/node_modules/.bin/vite" --host "$FRONTEND_HOST" --port "$FRONTEND_PORT" \
        >"$RUNTIME_DIR/frontend.log" 2>&1 < /dev/null &
    else
      nohup env "VITE_API_PROXY_TARGET=http://127.0.0.1:$BACKEND_PORT" \
        "$ROOT_DIR/node_modules/.bin/vite" --host "$FRONTEND_HOST" --port "$FRONTEND_PORT" \
        >"$RUNTIME_DIR/frontend.log" 2>&1 < /dev/null &
    fi
    echo $! >"$FRONTEND_PID_FILE"
  fi

  start_workbench
  if [[ -f "${CLOUDBIM_SCAN_BIM_INPUTS:-$RUNTIME_DIR/scan-bim-workbench/inputs.json}" ]]; then
    start_scan_bim
  fi

  log "Ready: http://127.0.0.1:$FRONTEND_PORT"
  log "Frontend bind: $FRONTEND_HOST:$FRONTEND_PORT"
  log "API:   http://127.0.0.1:$BACKEND_PORT"
  workbench_is_enabled && log "Point-cloud workbench: http://127.0.0.1:$WORKBENCH_PORT"
}

start_rebar_debug() {
  require_command node
  require_command curl
  require_command lsof
  mkdir -p "$RUNTIME_DIR"
  ensure_env_files
  load_ports
  [[ -x "$ROOT_DIR/node_modules/.bin/vite" ]] || fail "Run '$0 start' to install frontend dependencies first"
  curl -fsS "http://127.0.0.1:$BACKEND_PORT/health" >/dev/null || fail "Start the main stack with '$0 start' before starting the debug frontend"
  if pid_is_running "$REBAR_DEBUG_PID_FILE"; then
    log "Rebar debug already running (PID $(<"$REBAR_DEBUG_PID_FILE"), port $REBAR_DEBUG_PORT)"
    return
  fi
  port_is_available "$REBAR_DEBUG_PORT" || fail "Debug port $REBAR_DEBUG_PORT is occupied by an unmanaged process"
  log "Starting Scan-vs-BIM debug frontend on port $REBAR_DEBUG_PORT"
  (
    cd "$ROOT_DIR"
    if command -v setsid >/dev/null 2>&1; then
      exec setsid env "VITE_API_PROXY_TARGET=http://127.0.0.1:$BACKEND_PORT" \
        node "$ROOT_DIR/node_modules/vite/bin/vite.js" --config vite.rebar-debug.config.ts \
        --mode rebar-debug --host "$FRONTEND_HOST" --port "$REBAR_DEBUG_PORT" --strictPort
    else
      exec nohup env "VITE_API_PROXY_TARGET=http://127.0.0.1:$BACKEND_PORT" \
        node "$ROOT_DIR/node_modules/vite/bin/vite.js" --config vite.rebar-debug.config.ts \
        --mode rebar-debug --host "$FRONTEND_HOST" --port "$REBAR_DEBUG_PORT" --strictPort
    fi
  ) >"$RUNTIME_DIR/rebar-debug.log" 2>&1 < /dev/null &
  echo $! >"$REBAR_DEBUG_PID_FILE"
  for _ in {1..30}; do
    pid_is_running "$REBAR_DEBUG_PID_FILE" || fail "Debug frontend exited; inspect $RUNTIME_DIR/rebar-debug.log"
    if curl -fsS "http://127.0.0.1:$REBAR_DEBUG_PORT/" >/dev/null 2>&1; then
      log "Rebar debug ready: http://127.0.0.1:$REBAR_DEBUG_PORT (append the alignment page query to select assets)"
      return
    fi
    sleep 1
  done
  stop_process rebar-debug "$REBAR_DEBUG_PID_FILE"
  fail "Debug frontend did not become ready; inspect $RUNTIME_DIR/rebar-debug.log"
}

start_scan_bim() {
  ensure_env_files
  load_ports
  mkdir -p "$RUNTIME_DIR"
  local python="$RUNTIME_DIR/mesh-venv/bin/python"
  local inputs="${CLOUDBIM_SCAN_BIM_INPUTS:-$RUNTIME_DIR/scan-bim-workbench/inputs.json}"
  [[ -x "$python" ]] || fail "Scan-vs-BIM workbench requires $python"
  [[ -f "$inputs" ]] || fail "Missing input snapshot: $inputs (see docs/development/scan-vs-bim-workbench.md)"
  if ! pid_is_running "$SCAN_BIM_PID_FILE" && ! port_is_available "$SCAN_BIM_PORT"; then
    fail "Scan-vs-BIM workbench port $SCAN_BIM_PORT is already occupied"
  fi
  if ! pid_is_running "$SCAN_BIM_PID_FILE"; then
    log "Starting independent Scan-vs-BIM algorithm workbench"
    setsid "$ROOT_DIR/scripts/cloudbim-supervise.sh" \
      "$python" "$ROOT_DIR/scripts/scan-bim-debug.py" \
      --inputs "$inputs" --output "$RUNTIME_DIR/scan-bim-workbench/runs" \
      --host "$WORKBENCH_HOST" --port "$SCAN_BIM_PORT" --allow-host "$WORKBENCH_ALLOW_HOST" \
      >"$SCAN_BIM_LOG_FILE" 2>&1 < /dev/null &
    echo $! >"$SCAN_BIM_PID_FILE"
  fi
  for _ in {1..30}; do
    if curl -fsS "http://127.0.0.1:$SCAN_BIM_PORT/api/status" >/dev/null 2>&1; then
      log "Scan-vs-BIM workbench: http://127.0.0.1:$SCAN_BIM_PORT"
      return
    fi
    sleep 1
  done
  fail "Scan-vs-BIM workbench did not become ready; inspect $SCAN_BIM_LOG_FILE"
}

stop() {
  stop_process scan-bim-workbench "$SCAN_BIM_PID_FILE"
  stop_process rebar-debug "$REBAR_DEBUG_PID_FILE"
  stop_process point-cloud-workbench "$WORKBENCH_PID_FILE"
  stop_process frontend "$FRONTEND_PID_FILE"
  log "Stopping PostgreSQL, mesh service, and backend"
  docker compose -f "$ROOT_DIR/docker-compose.yml" down
}

status() {
  ensure_env_files
  load_ports
  log "Docker services:"
  docker compose -f "$ROOT_DIR/docker-compose.yml" ps
  if pid_is_running "$SCAN_BIM_PID_FILE" && curl -fsS "http://127.0.0.1:$SCAN_BIM_PORT/api/status" >/dev/null 2>&1; then
    log "Scan-vs-BIM workbench: running and healthy (port $SCAN_BIM_PORT)"
  elif pid_is_running "$SCAN_BIM_PID_FILE"; then
    log "Scan-vs-BIM workbench: supervised, currently restarting"
  elif ! port_is_available "$SCAN_BIM_PORT"; then
    log "Scan-vs-BIM workbench: unmanaged process on port $SCAN_BIM_PORT"
  else
    log "Scan-vs-BIM workbench: stopped (start with '$0 scan-bim-start')"
  fi
  if pid_is_running "$FRONTEND_PID_FILE"; then
    log "Frontend: running (PID $(<"$FRONTEND_PID_FILE"))"
  elif ! port_is_available "$FRONTEND_PORT"; then
    log "Frontend: running (port $FRONTEND_PORT)"
  else
    log "Frontend: stopped"
  fi
  if pid_is_running "$REBAR_DEBUG_PID_FILE"; then
    log "Rebar debug: running (PID $(<"$REBAR_DEBUG_PID_FILE"), port $REBAR_DEBUG_PORT)"
  elif ! port_is_available "$REBAR_DEBUG_PORT"; then
    log "Rebar debug: unmanaged process on port $REBAR_DEBUG_PORT"
  else
    log "Rebar debug: stopped (start with '$0 debug-start')"
  fi
  if ! workbench_is_enabled; then
    log "Point-cloud workbench: disabled"
  elif pid_is_running "$WORKBENCH_PID_FILE" && curl -fsS "http://127.0.0.1:$WORKBENCH_PORT/api/status" >/dev/null 2>&1; then
    log "Point-cloud workbench: running and healthy (supervisor PID $(<"$WORKBENCH_PID_FILE"), port $WORKBENCH_PORT)"
  elif pid_is_running "$WORKBENCH_PID_FILE"; then
    log "Point-cloud workbench: supervised, currently restarting (PID $(<"$WORKBENCH_PID_FILE"))"
  elif ! port_is_available "$WORKBENCH_PORT"; then
    log "Point-cloud workbench: unmanaged process on port $WORKBENCH_PORT"
  else
    log "Point-cloud workbench: stopped"
  fi
}

logs() {
  ensure_env_files
  load_ports
  docker compose -f "$ROOT_DIR/docker-compose.yml" logs -f backend postgres mesh-service &
  local compose_logs_pid=$!
  tail -n 100 -f "$RUNTIME_DIR/frontend.log" &
  local frontend_logs_pid=$!
  local log_pids=("$compose_logs_pid" "$frontend_logs_pid")
  if [[ -f "$SCAN_BIM_LOG_FILE" ]]; then
    tail -n 100 -f "$SCAN_BIM_LOG_FILE" &
    log_pids+=("$!")
  fi
  if [[ -f "$RUNTIME_DIR/rebar-debug.log" ]]; then
    tail -n 100 -f "$RUNTIME_DIR/rebar-debug.log" &
    log_pids+=("$!")
  fi
  if workbench_is_enabled && [[ -f "$WORKBENCH_LOG_FILE" ]]; then
    tail -n 100 -f "$WORKBENCH_LOG_FILE" &
    log_pids+=("$!")
  fi
  wait "${log_pids[@]}"
}

usage() {
  cat <<'EOF'
Usage: scripts/cloudbim-dev.sh <command>

Commands:
  start    Start dependency containers, backend, frontend, and enabled workbench.
  stop     Stop the frontend, workbench, backend, and dependency containers.
  restart  Stop then start the full development stack.
  status   Show process and container status.
  logs     Follow frontend, backend, and workbench logs.
  debug-start  Start the separate Scan-vs-BIM frontend on 5174 using the running backend.
  debug-stop   Stop only the Scan-vs-BIM frontend; leave the main stack running.
  scan-bim-start  Start the independent algorithm workbench on 8767 from a pinned input snapshot.
  scan-bim-stop   Stop only the algorithm workbench; retain its diagnostic runs.
EOF
}

case "${1:-}" in
  start) start ;;
  stop) stop ;;
  restart) stop; start ;;
  status) status ;;
  logs) logs ;;
  debug-start) start_rebar_debug ;;
  debug-stop) stop_process rebar-debug "$REBAR_DEBUG_PID_FILE" ;;
  scan-bim-start) start_scan_bim ;;
  scan-bim-stop) stop_process scan-bim-workbench "$SCAN_BIM_PID_FILE" ;;
  *) usage; exit 1 ;;
esac
