#!/usr/bin/env bash
#
# Cabo - build the image and (re)start the container.
#
# Usage:   ./build.sh            build + start
#          ./build.sh --fresh    build from scratch (no cache) + start
#
# Only ever touches the "cabo" container and the "cabo:local" image.
# Uses Docker's classic builder (no buildx needed).

set -euo pipefail

IMAGE="cabo:local"
CONTAINER="cabo"
PROJECT="cabo"
HOST_PORT="8088"

cd "$(dirname "$(readlink -f "$0")")"

say()  { printf '\033[1;35m==>\033[0m %s\n' "$*"; }
ok()   { printf '\033[1;32m ✔\033[0m %s\n' "$*"; }
fail() { printf '\033[1;31m ✘ %s\033[0m\n' "$*" >&2; exit 1; }

# ------------------------------------------------------------------ checks
say "Checking files in $(pwd)"
for f in Dockerfile docker-compose.yml requirements.txt app/app.py app/game.py tools/generate_cards.py; do
  [[ -e "$f" ]] || fail "Missing $f - run this script from the Cabo project folder"
done
ok "Project files present"

command -v docker >/dev/null || fail "docker not found"
docker compose version >/dev/null 2>&1 || fail "'docker compose' not available"

# A container called 'cabo' that isn't ours?
if docker container inspect "$CONTAINER" >/dev/null 2>&1; then
  owner=$(docker container inspect -f '{{ index .Config.Labels "com.docker.compose.project" }}' "$CONTAINER" 2>/dev/null || true)
  [[ "$owner" == "$PROJECT" ]] || fail "A different container is already called '$CONTAINER' - not touching it"
  ok "Existing Cabo container found - it will be replaced"
fi

# Something else on the host port?
other=$(docker ps --format '{{.Names}} {{.Ports}}' | grep -E ":${HOST_PORT}->" | grep -v "^${CONTAINER} " || true)
[[ -z "$other" ]] || fail "Port ${HOST_PORT} is used by container: ${other%% *}"
if ! docker ps --format '{{.Names}}' | grep -qx "$CONTAINER"; then
  if command -v ss >/dev/null && ss -ltn 2>/dev/null | grep -qE "[:.]${HOST_PORT}\s"; then
    fail "Port ${HOST_PORT} is already in use on this host"
  fi
fi
ok "Port ${HOST_PORT} is free"

# ------------------------------------------------------------------ build
OLD_ID=$(docker image inspect -f '{{.Id}}' "$IMAGE" 2>/dev/null || true)
BUILD_ARGS=(-t "$IMAGE")
if [[ "${1:-}" == "--fresh" ]]; then
  BUILD_ARGS+=(--no-cache --pull)
  say "Building $IMAGE from scratch"
else
  say "Building $IMAGE"
fi
DOCKER_BUILDKIT=0 docker build "${BUILD_ARGS[@]}" . 2>&1 | grep -v "legacy builder is deprecated" || true
docker image inspect "$IMAGE" >/dev/null 2>&1 || fail "Build failed - see output above"
ok "Image built"

# ------------------------------------------------------------------ run
say "Starting container"
docker compose up -d --no-build
ok "Container started"

say "Waiting for health check"
for i in $(seq 1 30); do
  state=$(docker inspect -f '{{.State.Status}} {{if .State.Health}}{{.State.Health.Status}}{{end}}' "$CONTAINER" 2>/dev/null || echo "missing")
  case "$state" in
    "running healthy") ok "Cabo is healthy"; break ;;
    running*)          sleep 2 ;;
    *)                 docker logs --tail 30 "$CONTAINER" 2>&1 || true; fail "Container is not running ($state)" ;;
  esac
  [[ $i -eq 30 ]] && { docker logs --tail 30 "$CONTAINER"; fail "Timed out waiting for health check"; }
done

# Remove the previous Cabo image left behind by a rebuild (only that one image, nothing else)
NEW_ID=$(docker image inspect -f '{{.Id}}' "$IMAGE")
if [[ -n "$OLD_ID" && "$OLD_ID" != "$NEW_ID" ]]; then
  docker rmi "$OLD_ID" >/dev/null 2>&1 && ok "Removed previous Cabo image" || true
fi

ip=$(hostname -I 2>/dev/null | awk '{print $1}')
echo
ok "Play at:  http://${ip:-<server-ip>}:${HOST_PORT}/"
docker stats "$CONTAINER" --no-stream --format '    {{.Name}}: CPU {{.CPUPerc}}  RAM {{.MemUsage}}'
