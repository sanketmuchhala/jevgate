"""Send every sample to a running JevGate server; report verdicts and latency.

    uvicorn src.app:app                                   # shell 1
    python scripts/run_samples.py                         # one pass, hybrid mode
    python scripts/run_samples.py --runs 20 --mode fast --out outputs/bench.json

Requests go one at a time, so the numbers are latency, not throughput.
"""
from __future__ import annotations
import argparse
import asyncio
import json
import math
import time
from pathlib import Path
import httpx

ROOT = Path(__file__).resolve().parent.parent


def pct(values: list[float], q: float) -> float:
    """Nearest-rank percentile."""
    ordered = sorted(values)
    return ordered[max(0, math.ceil(q * len(ordered)) - 1)]


def summary(values: list[float]) -> dict[str, float]:
    return {"p50": pct(values, 0.50), "p95": pct(values, 0.95), "max": max(values), "n": len(values)}


async def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--url", default="http://127.0.0.1:8000")
    ap.add_argument("--samples", default=str(ROOT / "samples"))
    ap.add_argument("--runs", type=int, default=1, help="Requests per sample")
    ap.add_argument("--warmup", type=int, default=2, help="Untimed requests first, to open pooled connections")
    ap.add_argument("--mode", choices=["fast", "hybrid"], default="hybrid")
    ap.add_argument("--out", help="Write every run and the summary as JSON")
    args = ap.parse_args()

    samples = [(p.stem, json.loads(p.read_text())) for p in sorted(Path(args.samples).glob("*.json"))]
    runs: list[dict] = []
    async with httpx.AsyncClient(base_url=args.url, timeout=60) as client:
        health = (await client.get("/healthz")).json()
        for _ in range(args.warmup):
            body = {"document": samples[0][1]["document"], "mode": "fast"}
            (await client.post("/v1/triage", json=body)).raise_for_status()
        for name, sample in samples:
            for _ in range(args.runs):
                body = {"document": sample["document"], "metadata": sample.get("metadata", {}), "mode": args.mode}
                started = time.perf_counter()
                resp = await client.post("/v1/triage", json=body)
                rtt_ms = (time.perf_counter() - started) * 1000
                resp.raise_for_status()
                d = resp.json()
                runs.append({
                    "sample": name,
                    "designed_for": sample.get("designed_for"),
                    "verdict": d["verdict"],
                    "decided_by": d["decided_by"],
                    "offices": d["offices"],
                    "review_offices": d["review_offices"],
                    "priority": d["priority"],
                    "risk_index": d["risk_index"],
                    "model": d["model"],
                    "system_one_ms": d["timings"]["system_one_ms"],
                    "system_two_ms": d["timings"]["system_two_ms"],
                    "total_ms": d["timings"]["total_ms"],
                    "rtt_ms": round(rtt_ms, 1),
                    "escalations": [e["code"] for e in d["escalations"]],
                })

    print(f"server: {health}")
    print(f"mode={args.mode} runs/sample={args.runs}\n")
    header = f"{'sample':<22} {'designed for':<34} {'verdict':<13} {'decided by':<14} {'offices':<28} {'S1 p50':>7} {'total p50':>9}"
    print(header)
    print("-" * len(header))
    for name, sample in samples:
        mine = [r for r in runs if r["sample"] == name]
        verdicts = sorted({r["verdict"] for r in mine})
        offices = sorted({o for r in mine for o in r["offices"] + r["review_offices"]})
        s1 = [r["system_one_ms"] for r in mine if r["system_one_ms"] is not None]
        print(f"{name:<22} {str(sample.get('designed_for')):<34} {'/'.join(verdicts):<13} "
              f"{'/'.join(sorted({r['decided_by'] for r in mine})):<14} {','.join(offices):<28} "
              f"{pct(s1, 0.5) if s1 else float('nan'):>7.0f} {pct([r['total_ms'] for r in mine], 0.5):>9.0f}")

    s1_only = [r for r in runs if r["system_two_ms"] is None and r["system_one_ms"] is not None]
    report = {
        "server": health,
        "mode": args.mode,
        "runs_per_sample": args.runs,
        "system_one_ms": summary([r["system_one_ms"] for r in s1_only]) if s1_only else None,
        "system_one_path_total_ms": summary([r["total_ms"] for r in s1_only]) if s1_only else None,
        "system_one_path_rtt_ms": summary([r["rtt_ms"] for r in s1_only]) if s1_only else None,
        "system_one_path_under_500ms": f"{sum(r['rtt_ms'] < 500 for r in s1_only)}/{len(s1_only)}",
        "runs": runs,
    }
    print("\nDecisions made without System-2 (latency in ms):")
    for key in ("system_one_ms", "system_one_path_total_ms", "system_one_path_rtt_ms"):
        print(f"  {key:<26} {report[key]}")
    print(f"  {'round trip under 500 ms':<26} {report['system_one_path_under_500ms']}")
    if args.out:
        Path(args.out).parent.mkdir(parents=True, exist_ok=True)
        Path(args.out).write_text(json.dumps(report, indent=2) + "\n")
        print(f"\nwrote {args.out}")


if __name__ == "__main__":
    asyncio.run(main())
