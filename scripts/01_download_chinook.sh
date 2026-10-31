#!/usr/bin/env bash
# Télécharge la base de test Chinook (SQLite) dans data/.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
DATA_DIR="$ROOT/data"
mkdir -p "$DATA_DIR"

URL="https://github.com/lerocha/chinook-database/raw/master/ChinookDatabase/DataSources/Chinook_Sqlite.sqlite"
DEST="$DATA_DIR/Chinook_Sqlite.sqlite"

if [ -f "$DEST" ]; then
  echo "Chinook déjà présent : $DEST"
else
  echo "Téléchargement de Chinook..."
  curl -L "$URL" -o "$DEST"
  echo "OK -> $DEST"
fi
