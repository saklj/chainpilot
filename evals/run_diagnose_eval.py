"""Compare deterministic workflow and optional ReAct diagnosis on injected scenarios."""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path

import duckdb

REPO_ROOT = Path(__file__).resolve().parents[1]
API_ROOT = REPO_ROOT / "api"
if str(API_ROOT) not in sys.path:
    sys.path.insert(0, str(API_ROOT))

from agent.diagnose import diagnose_material  # noqa: E402
from agent.diagnose_workflow import diagnose_material_workflow  # noqa: E402
from agent.llm import DeepSeekClient  # noqa: E402
from agent.safe_sql import database_path  # noqa: E402

CONSTRUCTION_CATEGORY = {
    "单源+低库存+在途不足": "single_source_supply",
    "共用料高需求+库存薄": "shared_demand_competition",
    "长交期+零在途": "long_leadtime_no_po",
}
INPUT_USD_PER_MILLION = Decimal("0.14")
OUTPUT_USD_PER_MILLION = Decimal("0.28")
GT_PATH = REPO_ROOT / "data" / "ground_truth_scenarios.json"
RESULT_PATH = REPO_ROOT / "evals" / "results" / "diagnose_eval.json"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--with-llm", action="store_true")
    parser.add_argument("--repeat", type=int, default=1)
    args = parser.parse_args()
    if args.repeat <= 0:
        parser.error("--repeat must be positive")
    scenarios = json.loads(GT_PATH.read_text(encoding="utf-8"))
    connection = duckdb.connect(str(database_path()), read_only=True)
    llm = DeepSeekClient() if args.with_llm else None
    rows: list[dict[str, object]] = []
    try:
        for repeat in range(1, args.repeat + 1):
            for item in scenarios:
                expected = CONSTRUCTION_CATEGORY[item["construction"]]
                workflow = diagnose_material_workflow(connection, item["material_pn"])
                agent = diagnose_material(llm, connection, item["material_pn"]) if llm else None
                row = {
                    "repeat": repeat,
                    "scenario_id": item["scenario_id"],
                    "material_pn": item["material_pn"],
                    "expected": expected,
                    "workflow_category": workflow.category,
                    "workflow_steps": workflow.steps,
                    "agent_category": agent.category if agent else None,
                    "agent_steps": agent.steps if agent else None,
                    "prompt_tokens": agent.usage.prompt_tokens if agent else 0,
                    "completion_tokens": agent.usage.completion_tokens if agent else 0,
                }
                rows.append(row)
                print(
                    f"{item['scenario_id']} expected={expected} workflow={workflow.category} "
                    f"agent={row['agent_category']} steps={row['agent_steps'] or workflow.steps}"
                )
    finally:
        connection.close()
    workflow_hits = sum(row["workflow_category"] == row["expected"] for row in rows)
    agent_rows = [row for row in rows if row["agent_category"] is not None]
    agent_hits = sum(row["agent_category"] == row["expected"] for row in agent_rows)
    workflow_steps = [int(row["workflow_steps"]) for row in rows]
    agent_steps = [int(row["agent_steps"]) for row in agent_rows]
    prompt_tokens = sum(int(row["prompt_tokens"]) for row in rows)
    completion_tokens = sum(int(row["completion_tokens"]) for row in rows)
    cost = (
        Decimal(prompt_tokens) * INPUT_USD_PER_MILLION
        + Decimal(completion_tokens) * OUTPUT_USD_PER_MILLION
    ) / Decimal(1_000_000)
    summary = {
        "workflow_accuracy": workflow_hits / len(rows),
        "workflow_avg_steps": sum(workflow_steps) / len(workflow_steps),
        "workflow_steps_interval": [min(workflow_steps), max(workflow_steps)],
        "agent_accuracy": agent_hits / len(agent_rows) if agent_rows else None,
        "agent_avg_steps": (
            sum(agent_steps) / len(agent_steps)
            if agent_rows
            else None
        ),
        "agent_steps_interval": [min(agent_steps), max(agent_steps)] if agent_steps else None,
        "prompt_tokens": prompt_tokens,
        "completion_tokens": completion_tokens,
        "estimated_cost_usd": float(cost),
    }
    payload = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "parameters": {"with_llm": args.with_llm, "repeat": args.repeat},
        "rows": rows,
        "summary": summary,
    }
    RESULT_PATH.parent.mkdir(parents=True, exist_ok=True)
    RESULT_PATH.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
