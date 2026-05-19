#!/bin/bash
# Run RAGAS bench for all 4 domains sequentially. Designed to be run in background.
set -e

OUT_DIR=${1:-bench_ragas_v3}
mkdir -p "$OUT_DIR"

for D in pokemon techstack ods pure_land; do
  echo ""
  echo "=== $D ==="
  uv run ontorag eval bench \
    "examples/$D/goldset.jsonl" \
    --baseline ontorag_native \
    --schema "examples/$D/schema.ttl" \
    --data "examples/$D/data.ttl" \
    --lang ko --with-ragas \
    --output "$OUT_DIR/${D}_ragas.json" 2>&1 | tail -20
  echo "→ $OUT_DIR/${D}_ragas.json done"
  sleep 30
done

echo ""
echo "All 4 domains complete. Outputs in $OUT_DIR/"
