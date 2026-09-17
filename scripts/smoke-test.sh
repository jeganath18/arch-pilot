#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"

python3 -m py_compile "$ROOT/scanner/scanner.py" "$ROOT"/functions/*/app.py

echo "== ARM-compatible fixture =="
python3 "$ROOT/scanner/scanner.py" "$ROOT/examples/arm-compatible" | python3 -c 'import json,sys; d=json.load(sys.stdin); assert d["verdict"]=="native_arm64"; print(d["verdict"])'

echo "== x86-required fixture =="
python3 "$ROOT/scanner/scanner.py" "$ROOT/examples/x86-required" | python3 -c 'import json,sys; d=json.load(sys.stdin); assert d["verdict"]=="x86_required"; print(d["verdict"])'

echo "== ambiguous fixture =="
python3 "$ROOT/scanner/scanner.py" "$ROOT/examples/ambiguous" | python3 -c 'import json,sys; d=json.load(sys.stdin); assert d["verdict"]=="ambiguous"; print(d["verdict"])'

echo "Smoke tests passed."
