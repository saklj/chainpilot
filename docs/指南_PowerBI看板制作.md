# Power BI 看板制作指南（ChainPilot 双轨 · 手把手版）

> 目标读者：在 Windows 上用 Power BI Desktop 复现 ChainPilot 风险看板的任何人。
> 前置：已运行 `python data/scripts/export_bi.py`，拿到 `data/processed/bi/` 下 5 张 CSV。
> 产出：两页报表（当期风险总览 / 九期风险趋势）+ 截图两张（进 README）。

## 0. 准备（约 10 分钟）

1. Windows 上安装 **Power BI Desktop**（Microsoft Store 搜索，免费，无需登录也能做报表和截图）
2. 把整个 `bi/` 文件夹拷到 Windows（网盘/U 盘均可，总量几 MB）

5 张表一句话速览：

| 文件 | 角色 | 内容 |
|---|---|---|
| fact_material_risk | 事实表 | 物料 × 评估日的风险指标（9 期 × 300 = 2,700 行） |
| dim_material | 维度 | 物料档案（名称/commodity/item group） |
| dim_supplier | 维度 | 供应商档案 |
| bridge_supply_split | 桥表 | 物料-供应商多对多（份额/交期/MOQ） |
| dim_date | 日期维 | 9 个评估日，`is_current` 标记最新一期 |

## 1. 导入数据（5 分钟）

主页 → **获取数据 → 文本/CSV** → 依次选 5 个 CSV → 每个点「加载」（不需要"转换数据"）。

## 2. 建关系（5 分钟）——星型模型的灵魂

左侧切到**模型视图**。Power BI 可能已自动猜出部分关系，核对成下面四条（拖字段到字段即可建立；方向和基数在弹窗里选）：

| 从（多端） | 到（一端） | 基数 | 交叉筛选 |
|---|---|---|---|
| fact_material_risk[material_pn] | dim_material[material_pn] | 多对一 | 单向 |
| fact_material_risk[eval_date] | dim_date[date] | 多对一 | 单向 |
| bridge_supply_split[material_pn] | dim_material[material_pn] | 多对一 | **双向** |
| bridge_supply_split[supplier_id] | dim_supplier[supplier_id] | 多对一 | 单向 |

> 为什么桥表那条设双向：想按供应商过滤出"它供的物料的风险"，筛选要能从 supplier 穿过桥表流向 material——这是多对多建模的标准手法，面试常客。

## 3. 建度量（10 分钟）

建模视图 → 选中 fact_material_risk → 右键「新建度量值」，逐个粘贴：

```dax
红色物料数 = CALCULATE(COUNTROWS(fact_material_risk), fact_material_risk[risk_level] = "RED")
橙色物料数 = CALCULATE(COUNTROWS(fact_material_risk), fact_material_risk[risk_level] = "ORANGE")
总缺口量 = SUM(fact_material_risk[gap_qty])
红橙占比 = DIVIDE([红色物料数] + [橙色物料数], COUNTROWS(fact_material_risk))
```

> 度量值是"随筛选上下文重算的公式"——同一个 [红色物料数]，在当期页显示 20，在趋势图里按期各算各的。这就是 DAX 相对 Excel 公式的本质区别。

## 4. 页面一：当期风险总览（20 分钟）

**先加页面级筛选器**：把 `dim_date[is_current]` 拖到「此页上的筛选器」，勾 True——整页锁定最新一期。

四块视觉对象：

1. **KPI 卡 × 4**（视觉对象「卡片」）：红色物料数 / 橙色物料数 / 总缺口量 / 红橙占比。预期值：20 / 16 / 54,978 / 12%——**和 Web 看板对不上就是哪里错了**，这组数字是我们的对账基准
2. **风险矩阵**（视觉对象「矩阵」）：行 = dim_material[commodity]，列 = fact[risk_level]，值 = 物料计数；「条件格式 → 背景色」按值上色
3. **集中度散点**（散点图）：X = fact[supplier_concentration]，Y = fact[gap_qty]，图例 = fact[risk_level]，详细信息 = material_pn。右上角那撮点就是"单源 + 大缺口"的高危区——这张图是叙事主角
4. **供货物料数 Top10**（条形图）：轴 = dim_supplier[supplier_name]，值 = bridge 行计数，筛选器取前 10

**风险四色**（每个视觉的图例颜色手动设为与 Web 看板一致）：
RED `#e5484d` · ORANGE `#f76b15` · YELLOW `#ffb224` · GREEN `#30a46c`

## 5. 页面二：九期风险趋势（10 分钟）

不加 is_current 筛选（要全部 9 期）：

1. **折线图**：X = dim_date[date]，Y = [红色物料数] 和 [橙色物料数] 两条线——预期红线从 27 波动降到 15 再跳回 20
2. **面积图**：X = dim_date[date]，Y = [总缺口量]——预期从 47,416 降至 24,277 后回升到 54,978

## 6. 交付（5 分钟）

1. 保存为 `chainpilot.pbix`（留在本机即可，不入仓）
2. 两页各截一张全页图，命名 `powerbi_overview.png` / `powerbi_trend.png`，发给指挥落 `docs/assets/` 并进 README
3. 顺手体验一下交叉过滤：点矩阵里 "ADDITIVE×RED" 那格，看散点图联动——这就是自研看板与 BI 工具的差距所在（demo 讲这个很直观）

## 常见坑

- CSV 中文乱码 → 导入时编码选 UTF-8（导出脚本已写 UTF-8-SIG，一般不会踩）
- 关系方向拖反 → 模型视图里箭头应从"一端"指向筛选流向；对照上表基数列
- KPI 数字对不上 → 九成是忘了页面筛选 is_current=True（把 9 期加总了）
