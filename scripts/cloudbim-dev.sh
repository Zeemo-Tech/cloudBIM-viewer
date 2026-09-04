#!/usr/bin/env bash

set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
BACKEND_DIR="$ROOT_DIR/backend"
RUNTIME_DIR="$ROOT_DIR/.cloudbim"
BACKEND_PID_FILE="$RUNTIME_DIR/backend.pid"
FRONTEND_PID_FILE="$RUNTIME_DIR/frontend.pid"

DB_PORT="${CLOUDBIM_DB_PORT:-15432}"
MESH_SERVICE_PORT="${CLOUDBIM_MESH_SERVICE_PORT:-18001}"
BACKEND_PORT="${CLOUDBIM_BACKEND_PORT:-8090}"
FRONTEND_PORT="${CLOUDBIM_FRONTEND_PORT:-5173}"

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
  ! ss -ltn "( sport = :$port )" 2>/dev/null | grep -q ":$port"
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

  if [[ ! -f "$BACKEND_DIR/.env" ]]; then
    cp "$BACKEND_DIR/.env.example" "$BACKEND_DIR/.env"
    set_env_value "$BACKEND_DIR/.env" ADDR ":$BACKEND_PORT"
    set_env_value "$BACKEND_DIR/.env" DB_PORT "$DB_PORT"
    set_env_value "$BACKEND_DIR/.env" MESH_SERVICE_URL "http://127.0.0.1:$MESH_SERVICE_PORT"
    log "Created backend/.env"
  fi
}

load_ports() {
  DB_PORT="$(read_env_value "$ROOT_DIR/.env" DB_PORT "$DB_PORT")"
  MESH_SERVICE_PORT="$(read_env_value "$ROOT_DIR/.env" MESH_SERVICE_PORT "$MESH_SERVICE_PORT")"
  local backend_addr
  backend_addr="$(read_env_value "$BACKEND_DIR/.env" ADDR ":$BACKEND_PORT")"
  BACKEND_PORT="${backend_addr#:}"
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

start() {
  require_command docker
  require_command go
  require_command npm
  require_command curl
  require_command ss
  require_command setsid

  mkdir -p "$RUNTIME_DIR"
  ensure_env_files
  load_ports

  if ! pid_is_running "$BACKEND_PID_FILE" && ! port_is_available "$BACKEND_PORT"; then
    fail "Port $BACKEND_PORT is already in use"
  fi
  if ! pid_is_running "$FRONTEND_PID_FILE" && ! port_is_available "$FRONTEND_PORT"; then
    fail "Port $FRONTEND_PORT is already in use"
  fi

  log "Starting PostgreSQL and mesh service"
  docker compose -f "$ROOT_DIR/docker-compose.yml" up -d postgres mesh-service
  wait_for_postgres

  if [[ ! -d "$ROOT_DIR/node_modules" ]]; then
    log "Installing frontend dependencies"
    (cd "$ROOT_DIR" && npm ci)
  fi

  if ! pid_is_running "$BACKEND_PID_FILE"; then
    log "Building and starting backend"
    (cd "$BACKEND_DIR" && go build -o "$RUNTIME_DIR/cloudbim-backend" .)
    (
      cd "$BACKEND_DIR"
      setsid "$RUNTIME_DIR/cloudbim-backend" >"$RUNTIME_DIR/backend.log" 2>&1 < /dev/null &
      echo $! >"$BACKEND_PID_FILE"
    )
  fi
  wait_for_backend

  if ! pid_is_running "$FRONTEND_PID_FILE"; then
    log "Starting frontend"
    setsid env "VITE_API_PROXY_TARGET=http://127.0.0.1:$BACKEND_PORT" \
      "$ROOT_DIR/node_modules/.bin/vite" --host 127.0.0.1 --port "$FRONTEND_PORT" \
      >"$RUNTIME_DIR/frontend.log" 2>&1 < /dev/null &
    echo $! >"$FRONTEND_PID_FILE"
  fi

  log "Ready: http://127.0.0.1:$FRONTEND_PORT"
  log "API:   http://127.0.0.1:$BACKEND_PORT"
}

stop() {
  stop_process frontend "$FRONTEND_PID_FILE"
  stop_process backend "$BACKEND_PID_FILE"
  log "Stopping PostgreSQL and mesh service"
  docker compose -f "$ROOT_DIR/docker-compose.yml" down
}

status() {
  if pid_is_running "$BACKEND_PID_FILE"; then
    log "Backend: running (PID $(<"$BACKEND_PID_FILE"))"
  else
    log "Backend: stopped"
  fi
  if pid_is_running "$FRONTEND_PID_FILE"; then
    log "Frontend: running (PID $(<"$FRONTEND_PID_FILE"))"
  else
    log "Frontend: stopped"
  fi
  docker compose -f "$ROOT_DIR/docker-compose.yml" ps
}

logs() {
  tail -n 100 -f "$RUNTIME_DIR/backend.log" "$RUNTIME_DIR/frontend.log"
}

usage() {
  cat <<'EOF'
Usage: scripts/cloudbim-dev.sh <command>

Commands:
  start    Start dependency containers, backend, and frontend.
  stop     Stop the frontend, backend, and dependency containers.
  restart  Stop then start the full development stack.
  status   Show process and container status.
  logs     Follow frontend and backend logs.
EOF
}

case "${1:-}" in
  start) start ;;
  stop) stop ;;
  restart) stop; start ;;
  status) status ;;
  logs) logs ;;
  *) usage; exit 1 ;;
esac
