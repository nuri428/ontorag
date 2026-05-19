"""Compare v0.4.0 baseline RAGAS scores vs v3 (post C1+C2 context engineering)."""
import json
from pathlib import Path

DOMAINS = ["pokemon", "techstack", "ods", "pure_land"]
METRICS = [
    ("avg_ragas_faithfulness", "Faithfulness"),
    ("avg_ragas_answer_correctness", "Correctness"),
    ("avg_ragas_answer_relevancy", "Relevancy"),
    ("avg_latency_ms", "latency"),
    ("avg_tool_calls", "tools"),
    ("avg_hallucination_rate", "hallucination"),
    ("avg_citation_coverage", "citation"),
]


def fmt(v, key):
    if v is None:
        return "  N/A"
    if key == "avg_latency_ms":
        return f"{v:6.0f}"
    return f"{v:5.3f}"


def delta(a, b, key):
    if a is None or b is None:
        return "    -"
    if key == "avg_latency_ms":
        d = (b - a) / a * 100 if a else 0
        return f"{d:+5.1f}%"
    d = b - a
    return f"{d:+5.3f}"


print(f"{'domain':12} {'metric':15} {'v0.4.0':>8} {'v3':>8} {'delta':>10}")
print("-" * 60)

for domain in DOMAINS:
    v04_path = Path(f"examples/{domain}/bench_results/ontorag_native_gpt4o.json")
    v3_path = Path(f"bench_ragas_v3/{domain}_ragas.json")
    if not v04_path.exists() or not v3_path.exists():
        print(f"{domain:12} (skipped — file missing)")
        continue
    v04 = json.loads(v04_path.read_text())["aggregate"]
    v3 = json.loads(v3_path.read_text())["aggregate"]
    for key, label in METRICS:
        a = v04.get(key)
        b = v3.get(key)
        print(f"{domain:12} {label:15} {fmt(a, key):>8} {fmt(b, key):>8} {delta(a, b, key):>10}")
    print()
