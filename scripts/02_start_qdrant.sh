#!/usr/bin/env bash
# Démarre Qdrant en local via Docker (persistance dans ./qdrant_storage).
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
STORAGE="$ROOT/qdrant_storage"
mkdir -p "$STORAGE"

# Stoppe un éventuel conteneur existant
docker rm -f bi_qdrant 2>/dev/null || true

docker run -d --name bi_qdrant \
  -p 7000:6333 -p 7001:6334 \
  -v "$STORAGE:/qdrant/storage" \
  qdrant/qdrant

echo "Qdrant démarré sur http://localhost:7000 (dashboard : http://localhost:7000/dashboard)"
