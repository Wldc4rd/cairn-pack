#!/bin/sh
set -eu
if [ -z "${GC_CITY_PATH:-}" ] || [ -z "${GC_PACK_DIR:-}" ]; then
  echo "gc cairn init: missing Gas City pack context" >&2
  exit 1
fi
CAIRN_CITY_ROOT="${CAIRN_CITY_ROOT:-$GC_CITY_PATH}" \
  exec python3 "$GC_PACK_DIR/scripts/memory_admin.py" init "$@"
