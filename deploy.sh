#!/usr/bin/env bash
# Deploy the committed HEAD to the eink LXC and (re)start it.
#
#   ./deploy.sh            code only; keeps the server's .env
#   ./deploy.sh --env      also push .env.server as the server's .env
#
# Reaches the container through the Proxmox host over Tailscale, so it works
# off-LAN too. Override with EINK_HOST / EINK_JUMP.
set -euo pipefail

HOST="${EINK_HOST:-root@192.168.0.60}"
JUMP="${EINK_JUMP:-root@100.116.30.73}"
DIR=/opt/eink
SSH=(ssh -o BatchMode=yes -J "$JUMP" "$HOST")

cd "$(dirname "$0")"
if [[ -n "$(git status --porcelain -- . ':!data' ':!.env*')" ]]; then
  echo "Uncommitted changes; commit first (deploy ships HEAD)." >&2
  exit 1
fi

git archive --format=tar HEAD | "${SSH[@]}" "mkdir -p $DIR && tar -x -C $DIR"

if [[ "${1:-}" == "--env" ]]; then
  "${SSH[@]}" "cat > $DIR/.env && chmod 600 $DIR/.env" < .env.server
fi

"${SSH[@]}" "cd $DIR && docker compose up -d --build 2>&1 | tail -3 && docker compose ps --format '{{.Name}} {{.Status}}'"
