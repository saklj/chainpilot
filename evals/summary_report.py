"""ChainPilot 全量跑分总报告生成器 —— 指标汇总器 + 漂移检测器（M6-T1）。

设计立场：三块指标均已封版留痕，LLM 评测重跑结果会波动（温度 0 ≠ 可复现，
见 docs/评测_问数Agent.md §5），因此本脚本**不重跑任何评测**——只读三个权威源
（forecast_metrics 表 / material_risk 表 + GT 清单 / 封版三连测结果文件），
重新计算指标并与内置封版常数比对：一致才生成 `docs/评测_总报告.md`，
任何漂移都打印指标名与两值、以非零码退出且不写出文件。
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import duckdb

REPO_ROOT = Path(__file__).resolve().parents[1]
API_ROOT = REPO_ROOT / "api"
if str(API_ROOT) not in sys.path:
    sys.path.insert(0, str(API_ROOT))

from agent.safe_sql import database_path  # noqa: E402

REPORT_PATH = REPO_ROOT / "docs" / "评测_总报告.md"
RESULTS_DIR = Path(__file__).with_name("results")
GROUND_TRUTH_PATH = REPO_ROOT / "data" / "ground_truth_scenarios.json"

# 封版常数。数值来自三份评测报告（docs/评测_预测对比.md §5/§7b、docs/评测_风险分级.md、
# docs/评测_问数Agent.md §1）；修改必须由指挥批准，并在对应评测报告中留痕新旧口径。
# 比对规则：int 严格相等；float 按封版常数自身的小数位数四舍五入后严格相等。
FROZEN_FORECAST: dict[str, float | int] = {
    "metric_rows": 9,
    "lightgbm_mape": 47.47,
    "seasonal_naive_mape": 56.35,
    "mape_improvement_pct": 15.76,
    "lightgbm_wmape": 31.85,
    "forecast_accuracy_pct": 68.2,
    "wmape_improvement_pct": 6.9,
    "lightgbm_wrmsse": 0.885,
    "seasonal_naive_wrmsse": 0.929,
}
FROZEN_RISK: dict[str, float | int] = {
    "gt_total": 10,
    "gt_recall": 10,
    "red_count": 20,
    "orange_count": 16,
    "yellow_count": 66,
    "green_count": 198,
    "total_gap_qty": 54978,
    "yellow_green_gap_qty": 0,
}
FROZEN_CHAT_FILES = (
    "eval_20260714_213712.json",
    "eval_20260714_214218.json",
    "eval_20260714_215535.json",
)
FROZEN_CHAT: dict[str, float | int] = {
    "sql_execution_pct_run1": 92.5,
    "sql_execution_pct_run2": 85.0,
    "sql_execution_pct_run3": 85.0,
    "answer_numeric_pct_run1": 97.5,
    "answer_numeric_pct_run2": 92.5,
    "answer_numeric_pct_run3": 95.0,
    "adversarial_pct_run1": 100.0,
    "adversarial_pct_run2": 100.0,
    "adversarial_pct_run3": 100.0,
    "estimated_cost_usd_run1": 0.03,
    "estimated_cost_usd_run2": 0.03,
    "estimated_cost_usd_run3": 0.03,
}

MODEL_ORDER = ("seasonal_naive", "ets", "lightgbm")
MODEL_LABELS = {
    "seasonal_naive": "seasonal_naive（基线）",
    "ets": "ets",
    "lightgbm": "**lightgbm**（选型）",
}


def _decimals(value: float) -> int:
    text = repr(value)
    return len(text.split(".")[1]) if "." in text else 0


def drift_errors(
    recomputed: dict[str, float | int], frozen: dict[str, float | int]
) -> list[str]:
    """封版比对：int 严格相等，float 按封版精度 round 后严格相等；漂移信息指名道姓。"""
    errors: list[str] = []
    for name, frozen_value in frozen.items():
        if name not in recomputed:
            errors.append(f"metric_missing: {name}")
            continue
        actual = recomputed[name]
        if isinstance(frozen_value, int):
            matched = actual == frozen_value
        else:
            matched = round(float(actual), _decimals(frozen_value)) == frozen_value
        if not matched:
            errors.append(
                f"metric_drift: {name}: frozen={frozen_value} recomputed={float(actual):.4f}"
            )
    return errors


def collect_forecast(
    connection: duckdb.DuckDBPyConnection,
) -> tuple[dict[str, float | int], dict[str, tuple[float, float, float]]]:
    """源 A：forecast_metrics 表 → 三折均值与改善率；同时返回三模型均值表供渲染。"""
    row_count = connection.execute("SELECT COUNT(*) FROM forecast_metrics").fetchone()[0]
    rows = connection.execute(
        "SELECT model_name, AVG(mape), AVG(wmape), AVG(wrmsse) "
        "FROM forecast_metrics GROUP BY model_name"
    ).fetchall()
    means = {row[0]: (float(row[1]), float(row[2]), float(row[3])) for row in rows}
    lgbm_mape, lgbm_wmape, lgbm_wrmsse = means["lightgbm"]
    naive_mape, naive_wmape, naive_wrmsse = means["seasonal_naive"]
    recomputed: dict[str, float | int] = {
        "metric_rows": int(row_count),
        "lightgbm_mape": lgbm_mape,
        "seasonal_naive_mape": naive_mape,
        "mape_improvement_pct": (naive_mape - lgbm_mape) / naive_mape * 100,
        "lightgbm_wmape": lgbm_wmape,
        "forecast_accuracy_pct": 100 - lgbm_wmape,
        "wmape_improvement_pct": (naive_wmape - lgbm_wmape) / naive_wmape * 100,
        "lightgbm_wrmsse": lgbm_wrmsse,
        "seasonal_naive_wrmsse": naive_wrmsse,
    }
    return recomputed, means


def collect_risk(connection: duckdb.DuckDBPyConnection) -> dict[str, float | int]:
    """源 B：material_risk（MAX(eval_date)）+ ground_truth_scenarios.json。"""
    eval_date = connection.execute("SELECT MAX(eval_date) FROM material_risk").fetchone()[0]
    counts = dict(
        connection.execute(
            "SELECT risk_level, COUNT(*) FROM material_risk WHERE eval_date = ? "
            "GROUP BY risk_level",
            [eval_date],
        ).fetchall()
    )
    total_gap = connection.execute(
        "SELECT COALESCE(SUM(gap_qty), 0) FROM material_risk WHERE eval_date = ?",
        [eval_date],
    ).fetchone()[0]
    yellow_green_gap = connection.execute(
        "SELECT COALESCE(SUM(gap_qty), 0) FROM material_risk WHERE eval_date = ? "
        "AND risk_level IN ('YELLOW', 'GREEN')",
        [eval_date],
    ).fetchone()[0]
    truth = json.loads(GROUND_TRUTH_PATH.read_text(encoding="utf-8"))
    gt_pns = [scenario["material_pn"] for scenario in truth]
    placeholders = ", ".join("?" for _ in gt_pns)
    recalled = connection.execute(
        "SELECT COUNT(*) FROM material_risk WHERE eval_date = ? "
        "AND risk_level IN ('RED', 'ORANGE') AND gap_qty > 0 "
        f"AND material_pn IN ({placeholders})",
        [eval_date, *gt_pns],
    ).fetchone()[0]
    return {
        "gt_total": len(gt_pns),
        "gt_recall": int(recalled),
        "red_count": int(counts.get("RED", 0)),
        "orange_count": int(counts.get("ORANGE", 0)),
        "yellow_count": int(counts.get("YELLOW", 0)),
        "green_count": int(counts.get("GREEN", 0)),
        "total_gap_qty": int(total_gap),
        "yellow_green_gap_qty": int(yellow_green_gap),
    }


def collect_chat() -> dict[str, float | int]:
    """源 C：封版三连测结果文件（文件名写死 = 问数报告 §1 的口径）。"""
    recomputed: dict[str, float | int] = {}
    for index, name in enumerate(FROZEN_CHAT_FILES, 1):
        summary = json.loads((RESULTS_DIR / name).read_text(encoding="utf-8"))["summary"]
        recomputed[f"sql_execution_pct_run{index}"] = float(
            summary["sql_execution_accuracy"]["rate_pct"]
        )
        recomputed[f"answer_numeric_pct_run{index}"] = float(
            summary["answer_numeric_accuracy"]["rate_pct"]
        )
        recomputed[f"adversarial_pct_run{index}"] = float(
            summary["adversarial_detection_rate"]["rate_pct"]
        )
        recomputed[f"estimated_cost_usd_run{index}"] = float(summary["estimated_cost_usd"])
    return recomputed


def render_report(means: dict[str, tuple[float, float, float]]) -> str:
    """渲染总报告。简历数字一律展示封版常数（已与重算断言一致）；均值表展示重算值。"""
    model_rows = "\n".join(
        f"| {MODEL_LABELS[model]} | {means[model][0]:.2f} | {means[model][1]:.2f} "
        f"| {means[model][2]:.3f} |"
        for model in MODEL_ORDER
    )
    frozen_files = "、".join(f"`{name}`" for name in FROZEN_CHAT_FILES)
    return f"""# ChainPilot 评测总报告（预测 / 风险 / 问数 封版指标汇总）

> ⚠️ 本文档由 `evals/summary_report.py` 生成，手改会在下次生成时被覆盖。
> 脚本在生成前会从三个权威数据源重算全部指标并与封版常数比对，任何不一致都会
> 拒绝生成并报错——因此本文档存在且是最新生成，即代表当次漂移检测全部通过。

## 1. 结论（简历数字）

| 指标 | 数值 | 权威源 | 详细报告 |
|---|---|---|---|
| 预测误差改善（LightGBM vs 季节朴素基线，MAPE） | **15.76%**（47.47 vs 56.35） | `forecast_metrics` 表 | [评测_预测对比](评测_预测对比.md) §5 |
| 预测准确度 FA = 1−WMAPE（日粒度，销量加权） | **68.2%**（较基线改善 6.9%） | `forecast_metrics` 表 | [评测_预测对比](评测_预测对比.md) §7b |
| WRMSSE（M5 竞赛官方口径） | **0.885**（基线 0.929） | `forecast_metrics` 表 | [评测_预测对比](评测_预测对比.md) §5 |
| 缺料 ground-truth 场景召回 | **10/10 = 100%** | `material_risk` 表 + GT 清单 | [评测_风险分级](评测_风险分级.md) §3 |
| NL2SQL 执行正确率（40 道数据题） | **85%~92.5%**（封版三连测 92.5 / 85.0 / 85.0） | 封版结果文件 ×3 | [评测_问数Agent](评测_问数Agent.md) §1 |
| 答案数字准确率 | **92.5%~97.5%** | 封版结果文件 ×3 | [评测_问数Agent](评测_问数Agent.md) §1 |
| 对抗题检出率（10 道） | **10/10 = 100%**（三连测全中） | 封版结果文件 ×3 | [评测_问数Agent](评测_问数Agent.md) §1 |
| 单轮全量评测成本 | **≈$0.03**（约 19 万 token，DeepSeek v4-flash） | 封版结果文件 ×3 | [评测_问数Agent](评测_问数Agent.md) §1 |

## 2. 预测块（M2 引擎：3 折滚动回测，日粒度 SKU 级，seed=42）

三折均值（`forecast_metrics` 表 9 行直出，MAPE/WMAPE 两位、WRMSSE 三位）：

| 模型 | MAPE | WMAPE | WRMSSE |
|---|---|---|---|
{model_rows}

口径说明：

- 看板 KPI 采用**日粒度 FA = 1−WMAPE = 68.2%**（销量加权，衡量"总量差多少"，贴近备料损失）；MAPE/WRMSSE 收进悬停 tooltip 作技术全景
- **诚实标注**：周粒度口径下季节朴素基线反超 LightGBM（WMAPE 20.29 vs 24.40）——周桶求和后"抄上周同星期"天然是强周预测器；LightGBM 的优势在日粒度点预测，这正是 MRP 逐日展开所需的粒度，故下游选型不变。**产品 KPI 禁用周粒度口径**，完整分析见 [评测_预测对比](评测_预测对比.md) §7b（该组数字为一次性分析、未入库，故不在本脚本断言范围内）

## 3. 风险块（M3 引擎：DOI / 缺口识别 / 四级分级）

- 缺料 ground-truth 场景召回 **10/10 = 100%**（每个注入场景均命中 RED/ORANGE 且缺口 > 0）
- 分级分布（最新 eval_date，300 物料）：**RED 20 / ORANGE 16 / YELLOW 66 / GREEN 198**；缺口件数合计 **54,978**，全部集中于红橙两级（黄绿缺口为 0，是分级逻辑的推论，本脚本每次生成时顺带验证）
- 误报观察：非 GT 的 RED 共 10 个，逐一核查**均存在可复算的预计负余额**（模拟层自然长出的真实缺口，非引擎误报）；本项目不宣称"误报率 = 0"，以含具体数字的缺口证据作抗辩，详见 [评测_风险分级](评测_风险分级.md) §4

## 4. 问数块（M4 Agent：NL→SQL + 证据护栏，50 题确定性判分）

封版三连测（温度 0、不重试、首次结果即判分）：

| 轮次 | 结果文件 | SQL 执行正确率 | 答案数字准确率 | 对抗检出 | 单轮成本 |
|---|---|---|---|---|---|
| 1 | `{FROZEN_CHAT_FILES[0]}` | 92.5% | 97.5% | 10/10 | ≈$0.03 |
| 2 | `{FROZEN_CHAT_FILES[1]}` | 85.0% | 92.5% | 10/10 | ≈$0.03 |
| 3 | `{FROZEN_CHAT_FILES[2]}` | 85.0% | 95.0% | 10/10 | ≈$0.03 |

- 50 题 = 20 模板 + 20 开放 + 10 对抗；判分为确定性代码（5 种 check 类型），无 LLM 裁判
- 对抗题六轮跑分（含调试轮）从未失手；温度 0 ≠ 可复现是重要工程发现，见 [评测_问数Agent](评测_问数Agent.md) §5

## 5. 数据源与复现

| 块 | 权威源 |
|---|---|
| 预测 | DuckDB `forecast_metrics` 表（3 模型 × 3 折） |
| 风险 | DuckDB `material_risk` 表（MAX(eval_date)）+ `data/ground_truth_scenarios.json` |
| 问数 | {frozen_files} |

复现（仓库根执行；只读打开数据库，零 LLM 调用，零网络）：

```bash
api/.venv/bin/python -m evals.summary_report
```

脚本重算全部指标并与封版常数（`evals/summary_report.py` 顶部 `FROZEN_*`）比对：
一致 → 重写本文档；任何漂移 → 打印指标名与期望/实际值，退出码 1，本文档保持原样。
"""


def build_report() -> tuple[str | None, list[str]]:
    """采集三源 → 漂移检测 → 渲染。返回 (报告文本或 None, 漂移错误列表)。"""
    connection = duckdb.connect(str(database_path()), read_only=True)
    try:
        forecast, means = collect_forecast(connection)
        risk = collect_risk(connection)
    finally:
        connection.close()
    chat = collect_chat()
    errors = (
        drift_errors(forecast, FROZEN_FORECAST)
        + drift_errors(risk, FROZEN_RISK)
        + drift_errors(chat, FROZEN_CHAT)
    )
    if errors:
        return None, errors
    return render_report(means), []


def main() -> int:
    text, errors = build_report()
    if errors:
        for error in errors:
            print(error, file=sys.stderr)
        print(f"漂移检测未通过（{len(errors)} 项），已拒绝生成总报告。", file=sys.stderr)
        return 1
    assert text is not None
    REPORT_PATH.write_text(text, encoding="utf-8")
    print(f"漂移检测通过，总报告已生成：{REPORT_PATH.relative_to(REPO_ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
