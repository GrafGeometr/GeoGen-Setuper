#!/usr/bin/env bash
set -euo pipefail
builder_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
if ! command -v python3 >/dev/null 2>&1; then
    echo 'Нужен Python 3.10+: sudo apt-get install python3' >&2
    exit 1
fi
exec python3 "$builder_dir/builder.py" "$@"
