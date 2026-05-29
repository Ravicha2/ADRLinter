#!/usr/bin/env bash
set -euo pipefail

if [[ "${1:-}" == "--build" ]]; then
  echo "Stopping containers..."
  docker-compose down
  echo "Rebuilding and starting..."
  docker-compose up --build -d
else
  echo "Restarting containers..."
  docker-compose restart
fi

echo "Following logs (Ctrl+C to detach)..."
docker-compose logs -f api neo4j