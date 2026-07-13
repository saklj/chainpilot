# ChainPilot

供应链智能分析 Agent —— 需求预测 · DOI 风险洞察 · 自然语言问数（带证据护栏）

> 🚧 开发中（2026-07 启动，4 周路线图见 [docs/00_项目蓝图.md](docs/00_项目蓝图.md)）

## 是什么

面向供应链计划员的 AI 分析助手：接入销量、BOM、库存、供应商多源数据，自动完成需求预测与物料风险分级；业务人员用自然语言提问，获得**每个数字都可回查溯源**的答案与自动周报。

- 数据：Kaggle M5 真实销量（降采样）+ 可解释规则生成的模拟制造层（BOM/供应商/库存），含 ground-truth 缺料场景
- 预测：statsforecast baseline vs LightGBM，滚动回测量化对比
- 风险：MRP-lite 展开 → DOI / 缺口 / 供应商集中度 → 红橙黄绿四级分级，规则可解释
- 问数 Agent：NL→SQL（DeepSeek）+ 只读安全执行 + **确定性证据护栏**（答案数值回查 SQL 结果，对不上拒答）
- 评测：50 题问数评测集 + 对抗样本 + 预测/风险指标全量回归

## 目录

```
web/    Next.js + TypeScript + Zod 前端（看板 / Chat 问数 / 周报）
api/    FastAPI 后端（agent / analytics）
data/   数据脚本与 DuckDB 库
docs/   蓝图、数据字典、模块清单、评测报告
evals/  评测集与跑分脚本
```

## Quickstart

```bash
# 待 M0/M1 完成后补全：
# 1. python data/scripts/build_db.py     # 重建数据库
# 2. uvicorn api.app.main:app --reload   # 启动后端
# 3. cd web && pnpm dev                  # 启动前端
```

## 文档

| 文档 | 内容 |
|---|---|
| [00_项目蓝图](docs/00_项目蓝图.md) | 定位、架构、8 项关键选型决策、4 周路线图 |
| [01_数据字典](docs/01_数据字典.md) | 全部表结构、风险分级规则、业务术语表 |
| [02_模块执行清单](docs/02_模块执行清单.md) | M0~M6 任务 checklist 与验收标准 |

## 声明

本项目使用公开数据集（Kaggle M5）与脚本生成的模拟数据，业务术语均为制造业通用概念，不包含任何企业内部数据。
