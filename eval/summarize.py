"""Per-arm summary of scored trials. The last stdout line is one JSON object of counts.

    python eval/summarize.py eval-runs/pilot
"""

from __future__ import annotations

import json
import statistics
import sys
from collections import defaultdict
from pathlib import Path


def summarize(rows: list) -> dict:
    by_arm = defaultdict(list)
    for r in rows:
        by_arm[r["arm"]].append(r)
    out = {}
    for arm, rs in sorted(by_arm.items()):
        costs = [r["cost_usd"] for r in rs if isinstance(r.get("cost_usd"), (int, float))]
        out[arm] = {
            "trials": len(rs),
            "stray_mean": round(statistics.mean(r["stray"] for r in rs), 3),
            "trials_with_stray": sum(1 for r in rs if r["stray"]),
            "stray_committed": sum(r["stray_committed"] for r in rs),
            "bulk_add_run": sum(r["bulk_add_run"] for r in rs),
            "commits": sum(r["commits"] for r in rs),
            "commits_conventional": sum(r["commits_conventional"] for r in rs),
            "hidden_pass": sum(1 for r in rs if r["hidden_pass"]),
            "errors": sum(1 for r in rs if r.get("error")),
            "plugin_loaded": sum(1 for r in rs if r.get("plugin_loaded")),
            "cost_usd_total": round(sum(costs), 4),
            "cost_usd_mean": round(statistics.mean(costs), 4) if costs else None,
            "stray_files": sorted({f for r in rs for f in r["stray_files"]}),
        }
    return out


def main() -> int:
    rows = [json.loads(p.read_text(encoding="utf-8")) for p in sorted(Path(sys.argv[1]).glob("*.score.json"))]
    summary = summarize(rows)
    for arm, s in summary.items():
        print(f"{arm:9} n={s['trials']:2} stray/task={s['stray_mean']:.2f} with-stray={s['trials_with_stray']} "
              f"bulk-add={s['bulk_add_run']} conventional={s['commits_conventional']}/{s['commits']} "
              f"hidden={s['hidden_pass']}/{s['trials']} cost=${s['cost_usd_total']:.2f} errors={s['errors']}")
    print(json.dumps(summary))
    return 0


if __name__ == "__main__":
    sys.exit(main())
