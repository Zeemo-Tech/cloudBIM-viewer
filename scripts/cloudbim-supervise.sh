#!/usr/bin/env bash

set -uo pipefail

if [[ "$#" -eq 0 ]]; then
  printf 'Usage: %s <command> [args...]\n' "$0" >&2
  exit 2
fi

child_pid=""

stop_child() {
  if [[ -n "$child_pid" ]] && kill -0 "$child_pid" 2>/dev/null; then
    kill "$child_pid" 2>/dev/null || true
    wait "$child_pid" 2>/dev/null || true
  fi
  exit 0
}

trap stop_child INT TERM HUP

while true; do
  printf '[cloudbim-supervisor] starting:'
  printf ' %q' "$@"
  printf '\n'

  "$@" &
  child_pid=$!
  wait "$child_pid"
  exit_code=$?
  child_pid=""

  printf '[cloudbim-supervisor] process exited with status %s; restarting in 2 seconds\n' "$exit_code" >&2
  sleep 2
done
