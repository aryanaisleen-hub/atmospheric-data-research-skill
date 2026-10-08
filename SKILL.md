---
name: atmospheric-data-research
description: >
  给 AI/Codex 使用的大气环境科研数据工作流 Skill。当用户提出大气/气溶胶/污染/火点/
  生物质燃烧/输送/气象相关的科研任务，需要下载 ERA5 与 NASA FIRMS 数据、申请并跟踪数据任务、
  检查数据质量、选择分析绘图方案、绘制论文级图件并自动读图解释、生成科研报告时使用。
  触发词：ERA5、FIRMS、火点、生物质燃烧、PM1、PM2.5、气溶胶、输送、风场、边界层、
  数据下载、科研绘图、滞后相关、analysis workflow、research data。
---

# atmospheric-data-research

> 这不是一个下载脚本，而是**给 AI 使用的科研数据全生命周期工作流**。
> 它把「用户需求 → 数据判断 → 申请 → 定时检查 → 下载 → 质检 → 分析 → 绘图 → 读图 → 解释 → 报告」
> 串成一条可断点续传、可审计、有明确用户确认节点的流水线。

---

## 1. Skill 用途

让 AI 能够：

1. 把用户的自然语言科研任务解析成结构化需求（区域 / 时间 / 对象 / 科学问题）；
2. **自动判断**需要哪些数据，并向用户说明「为什么要下载这些数据」；
3. 检查 API / KEY / 环境配置，缺 MAP_KEY 时提示用户**自行申请**；
4. 生成数据申请任务，**首次最多提交 3 个**，之后始终保持**并发不超过 5 个**；
5. 按**每小时**节奏检查申请状态，完成即下载，下载后释放名额并补充提交；
6. 下载完成后做**数据完整性 / 质量控制**与标准化整理；
7. 依据数据能力向用户**推荐分析与绘图方案**（A–F 共 26 项），而不是把图全画出来；
8. 用 matplotlib 绘制论文级图件（300 dpi，PNG + PDF/SVG）；
9. **实际打开并检查**每张图，发现问题自动修正（最多 3 次）；
10. 逐图给出四层解释，并汇总成 `analysis_report.md`。

**这不是通用绘图工具。** 它只服务于「大气环境观测 + 再分析 + 卫星火点」这一类科研数据。

---

## 2. 触发条件

当用户请求中出现以下任一情形时使用本 Skill：

- 明确提到 **ERA5 / CDS / FIRMS / VIIRS / MODIS** 数据；
- 提到 **火点 / 生物质燃烧 / FRP / 秸秆焚烧**；
- 提到 **PM1 / PM2.5 / 气溶胶 / 污染过程 / 输送 / 风场 / 边界层 / 降水清除**；
- 要求「下载某时段某区域的气象/火点数据并分析」；
- 要求「研究 A 与 B 的关系 / 滞后关系 / 输送条件」。

不适用：与大气科研无关的通用数据处理；实时业务预报；纯模型模拟（本 Skill 不做 WRF/CMAQ 运行）。

---

## 3. 用户需要提供的信息

| 信息 | 是否必须 | 缺失时的处理 |
|---|---|---|
| 研究目标 / 科学问题 | 必须 | 向用户确认 |
| **时间范围** | **必须** | **必须先向用户询问，不得擅自猜测** |
| **经纬度范围** | **必须** | **必须先向用户询问，不得擅自猜测** |
| 观测站（名称 + 经纬度） | 建议 | 若涉及站点对比则询问 |
| 观测变量（PM1/PM2.5/…） | 建议 | 默认 PM1 |
| 已有数据（观测 CSV 等） | 可选 | 询问路径 |
| 期望的分析/图形 | 可选 | 由 AI 推荐 |

> **硬约束**：`start` / `end` / `region` 任一缺失，`build_spec()` 会抛
> `MissingRequirementError`，AI 必须回到用户追问，**不能使用默认值代替**。

---

## 4. AI 如何判断数据需求

AI 先解析任务，再用 `planning.infer_data_needs()`（关键词启发式）得到建议，最后**向用户确认**：

| 研究关键词 | 建议数据 | 用途 |
|---|---|---|
| 火点 / 生物质燃烧 / FRP / 秸秆 | FIRMS VIIRS（或 MODIS） | 燃烧活动与源强 |
| 污染 / PM1 / 气溶胶 | FIRMS 火点 + ERA5 风场 | 源-受体关系 |
| 降水 / 湿清除 | ERA5 single `total_precipitation` | 湿沉降 |
| 边界层 / 混合 / 稀释 | ERA5 single `boundary_layer_height` | 垂直混合 |
| 温度 / 热力条件 | ERA5 single `2m_temperature` | 热力背景 |
| 输送 / 风场 / 环流 / 850-500 | ERA5 pressure `u,v` @ 850/700/600/500 hPa | 输送通道 |
| 上升/下沉 / 垂直输送 | ERA5 pressure `vertical_velocity` | 垂直运动 |

**必须向用户展示**「我建议下载以下数据，因为它们分别用于……」，并**等待用户确认后**才提交。

---

## 5. MAP_KEY 配置（FIRMS）

FIRMS 数据下载需要 `MAP_KEY`：

- **AI 无法代替用户申请**。提示用户到 <https://firms.modaps.eosdis.nasa.gov/api/map_key/> 免费申请。
- 配置到环境变量 `FIRMS_MAP_KEY`（PowerShell，仅当前窗口有效）：
  ```powershell
  $env:FIRMS_MAP_KEY = "你收到的 MAP_KEY"
  ```
- 代码只通过 `settings.firms_map_key()` 读取环境变量；**绝不**猜测、生成、写死 KEY。
- **绝不**把 KEY 写入代码 / SKILL.md / README / Git / 日志 / 报告。
- 日志中即使报错也只显示 `凭据已隐藏`。
- 若未配置：**停止 FIRMS 下载流程**，提示用户配置；ERA5 流程可继续。

`environment_report()` 会输出是否就绪（只显示 KEY 长度与前 2 位，如 `长度 32，前缀 ab***`）。

---

## 6. ERA5 数据申请

- 使用官方 `cdsapi`（需在用户主目录配置 `.cdsapirc`）。
- 数据集：`reanalysis-era5-single-levels`、`reanalysis-era5-pressure-levels`。
- 变量、气压层、请求默认值全部来自 `config/datasets.yaml`，**可扩展、不写死**。
- 气压层数据按「**月份 × 变量**」提交，规避 CDS 请求体积限制。
- 请求字段：`product_type / variable / year / month / day / time / area / data_format / download_format / pressure_level`。
- `area` 使用 CDS 顺序 **north, west, south, east**。
- 下载产物可能是 ZIP 或直接 NetCDF，统一解包 / 合并为单个 NetCDF 后**校验再落盘**。
- 绝不覆盖已有文件；校验失败保留文件供排查，不自动删除。

## 7. FIRMS 数据申请

- 使用 NASA FIRMS Area API（`/api/area/csv`）。
- 产品：`VIIRS_SNPP_SP`（默认）、`VIIRS_NOAA20_SP`、`MODIS_SP`、`MODIS_NRT`。
- 字段：`latitude, longitude, acq_date, acq_time, frp, confidence, daynight`（MODIS 另含 `satellite`）。
- **单次最多 5 天**，代码自动按 `firms_chunk_days` 分块，再合并为月度 CSV + manifest（含 SHA-256）。
- FIRMS 是直接下载型数据源（无排队），但仍纳入统一状态机：
  `PENDING → SUBMITTED → COMPLETED → DOWNLOADING → DOWNLOADED`。

---

## 8. 任务队列与并发限制（核心）

任务记录写在 `logs/data_requests.json`（`config/settings.yaml → workspace.requests_file`）。

**并发规则（严格实现于 `queue.py`）：**

1. **首次启动：最多同时提交 3 个**（`download.initial_submit`）。
2. 之后每当有任务完成并下载、**释放名额**，就**补充提交**新任务。
3. **任意时刻** `SUBMITTED + QUEUED + RUNNING` 合计 **≤ 5**（`download.max_concurrent`）。

```
首次：T1 SUBMITTED, T2 SUBMITTED, T3 SUBMITTED        （3 个）
检查：T1 COMPLETED, T2 QUEUED,   T3 COMPLETED
下载：T1 ✔, T3 ✔                                        （释放 2 个名额）
补充：T4 SUBMITTED, T5 SUBMITTED                        （仍 ≤ 5）
```

`queue_status()` 会给出 `phase / active / limit / max_concurrent / states` 供 AI 汇报。

## 9. 定时检查

- 默认**每 60 分钟**检查一次（`download.check_interval_minutes`）。
- 每次检查内容：是否 completed / failed / still queued / 正在下载 / 已下载 / 需重试。
- 发现 `COMPLETED` → 下载 → 校验 → 标记 `DOWNLOADED` → 释放名额 → 补充提交。
- 发现 `FAILED` → 记录错误 → 若 `attempts < max_retries` 标记 `RETRY` 并重提。
- **不做无限重试**：超过 `download.max_retries`（默认 3）则停在 `FAILED`。
- 状态查询瞬时失败（`lookup_error`）**不会**把已知任务误判为失败，避免重复提交。

## 10. 任务状态机

```
PENDING → SUBMITTED → QUEUED → RUNNING → COMPLETED → DOWNLOADING → DOWNLOADED(终态)
             ↘____________↓___________↘ FAILED ⇄ RETRY ↗
```

| 状态 | 含义 |
|---|---|
| PENDING | 已生成、未提交 |
| SUBMITTED | 已提交给提供方 |
| QUEUED | 提供方排队中 |
| RUNNING | 提供方处理中 |
| COMPLETED | 提供方完成、未下载 |
| DOWNLOADING | 正在下载 |
| DOWNLOADED | 已下载并校验通过（**终态**） |
| FAILED | 失败 |
| RETRY | 待重试 |

- 非法迁移会被 `assert_transition()` 拒绝（禁止模糊状态）。
- **每次状态变化写入 `history` 并追加日志**。

## 11. 断点续传与恢复

Codex 关闭 / 电脑重启 / 网络断开后，重新运行会：

1. 读取 `logs/data_requests.json`；
2. **不重新提交**已提交的任务（`task_id` 去重）；
3. **不重复下载**已下载的文件（文件存在即跳过）；
4. 从当前状态继续。

## 12. 数据质量控制

对每个已下载任务执行 `data_check/checks.py`：

**ERA5**：文件存在 → 大小 → 能否打开 → 时间范围 → 时间分辨率（须为 1 小时）→ 经纬度范围 →
变量是否齐全 → 缺失比例 → 单位（从 NetCDF metadata 读取，不自行换算）→ 重复时间 → 6σ 离群值 → 气压层。

**FIRMS**：文件存在 → 火点数量 → 经纬度 → 时间范围 → FRP（无负值）→ confidence → 重复记录。

结果写入 `outputs/tables/data_quality_report.csv`，并给出 `PASS / WARN / FAIL` 结论。

## 13. 数据整理与标准化

- 原始数据 `raw`：**只读，绝不修改或删除**。
- 整理后：`processed/`；分析结果：`results/`；图件：`outputs/figures/`；表格：`outputs/tables/`；日志：`logs/`。
- 统一：**时间 UTC**；经纬度字段固定为 `latitude / longitude`；区域固定为 `north / south / east / west`；
  **单位一律取自 NetCDF metadata**，未确认单位前不换算。

## 14. 分析方案推荐

数据就绪后**不要直接画图**。先由 `available_options()` 按「数据能力」过滤目录，
再由 `recommend_options()` 按研究问题关键词打分，**只推荐最合适的若干张**。

菜单分 A–F 六组共 26 项：

| 组 | 内容 |
|---|---|
| A 时间变化 | PM1 时间序列 / 火点数 / FRP / 双时间序列 |
| B 空间分布 | 火点分布 / FRP 网格 / 火点密度 / 风场 / 火点+风场 |
| C 气象分析 | 850 / 700 / 500 hPa 风场、PBLH、降水、温度 |
| D 相关性 | 火点-FRP-风速-降水-PBLH vs PM1 |
| E 时间滞后 | 火点 / FRP / 气象 → PM1 滞后相关 |
| F 综合图 | 火点+风场+PM1 / 火点+ERA5+PM1 / 污染过程综合 |

用户可：选编号、多选、要求推荐、自行描述。例如「画 5、11、21」。

## 15. 绘图

- 使用 `pandas / numpy / xarray / scipy / matplotlib`；**不使用 seaborn**。
- `cartopy` 为**可选**依赖：有则用其投影，无则降级为经纬度坐标轴，并用随 Skill 附带的
  Natural Earth 110m 国界矢量（`assets/boundaries/`）绘制国界。
- 默认 **300 dpi**，输出 **PNG**，并尽可能输出 **PDF / SVG**。
- 要求：坐标清楚、单位正确、图例清晰、colorbar 清晰、时间/经纬度格式合理。

## 16. 绘图后自动查看（重要）

绘制完成后 **AI 必须实际打开图片检查**，不能只看代码：

`plotting/review.py` 打开 PNG 并检查：是否生成 / 是否空白（近白像素占比）/ 图面内容是否过少 /
尺寸 / 内容是否被裁切（四边边框）/ 灰度层次是否足够（图例、colorbar）。

发现问题 → **自动修正重绘，最多 3 次**（`review.max_auto_fix`）。仍不通过则标记
`needs_attention` 并如实报告用户。

## 17. 自动解释每张图

每张图给出四层解释：

1. **图展示了什么**（caption）
2. **主要现象**（findings，含具体数值与日期）
3. **与研究问题的关系**（link）
4. **科学解释**（science，**措辞谨慎**）

措辞规范：使用「表明 / 与……一致 / 可能表明 / 可能反映」，
**不得仅凭相关性声称因果关系**。

## 18. 报告生成

`results/analysis_report.md` 包含：

1. 数据来源 2. 时间/空间范围 3. 下载的数据与队列状态 4. 数据质量检查 5. 分析方法
6. 图形列表与逐图解释 7. 综合科学解释 8. 不确定性 9. 后续建议

---

## 19. 标准工作流程（AI 应按此执行）

```
用户提出科研需求
  ↓ 1  解析科研任务（目标/时间/区域/对象/科学问题）
  ↓ 2  校验必填项；缺失则追问用户（不得猜测）
  ↓ 3  判断需要哪些数据 → 向用户说明理由 → 等待确认
  ↓ 4  检查环境：FIRMS_MAP_KEY / .cdsapirc
  ↓ 5  生成任务（planning.plan_tasks）→ 写入 logs/data_requests.json
  ↓ 6  首次提交最多 3 个
  ↓ 7  定时检查（默认 60 min）：status → download → 释放名额 → 补充提交（≤5）
  ↓ 8  全部 DOWNLOADED 后：数据质量检查
  ↓ 9  数据整理与标准化
  ↓ 10 向用户提供分析/绘图选项（AI 先推荐）
  ↓ 11 用户选择 → matplotlib 绘图
  ↓ 12 自动打开/查看图片 → 有问题自动修正（≤3 次）
  ↓ 13 逐图解释（四层）
  ↓ 14 汇总 → analysis_report.md
```

**需用户确认的操作**：大量/大范围下载、大量 API 请求、删除数据、覆盖原始数据。
**可自动执行**：数据检查、整理、统计、绘图、读图、图形解释。

---

## 20. 安全要求（不可违背）

- API KEY **绝不**写入代码 / SKILL.md / README / Git / 日志 / 报告。
- 一律通过环境变量（`FIRMS_MAP_KEY`）或用户主目录安全配置（`.cdsapirc`）。
- 日志与错误信息中**不得出现**完整 KEY；FIRMS 请求失败只报「凭据已隐藏」。
- 原始数据（raw）**只读**；脚本不自动删除任何文件。
- 已有文件未通过校验时**停止并保留**，由用户决定如何处理。

---

## 21. 目录结构

```
atmospheric-data-research/
├── SKILL.md                     ← 本文件（AI 工作流说明）
├── README.md                    ← 面向人的说明
├── config/
│   ├── settings.yaml            ← 工作区、并发、绘图、审查参数
│   ├── datasets.yaml            ← ERA5 / FIRMS 数据目录（可扩展）
│   ├── variables.yaml           ← 变量显示元数据
│   └── analysis_catalog.yaml    ← A–F 共 26 项分析方案
├── scripts/
│   ├── run.py                   ← 命令行入口
│   └── research_kit/
│       ├── settings.py          ← 配置与路径
│       ├── states.py            ← 状态机
│       ├── store.py             ← 任务记录（原子读写）
│       ├── queue.py             ← 并发调度（3 首提 / 5 上限）
│       ├── planning.py          ← 需求解析与任务生成
│       ├── validation.py        ← 通用校验与安全落盘
│       ├── era5/client.py       ← ERA5/CDS 客户端
│       ├── firms/client.py      ← FIRMS 客户端
│       ├── data_check/checks.py ← 数据质量检查
│       ├── prepare.py           ← 数据整理与标准化
│       ├── analysis/            ← 方案推荐 + 统计
│       ├── plotting/            ← 绘图 + 自动读图 + 边界矢量
│       ├── report.py            ← 报告生成
│       ├── simulate.py          ← 模拟后端（测试/演示）
│       ├── orchestrator.py      ← 全流程编排
│       └── cli.py               ← 命令行实现
├── assets/boundaries/           ← Natural Earth 110m 国界（地图背景）
├── templates/                   ← 报告/请求清单模板
├── tests/                       ← 单元测试 + 端到端测试
└── examples/                    ← 示例任务与端到端演示
```

---

## 22. 命令参考

在 Skill 根目录执行（用你的 Python 环境）：

```bash
python scripts/run.py env      --study examples/study_ngari_2022.json   # 环境/凭据检查
python scripts/run.py plan     --study examples/study_ngari_2022.json   # 需求解析 + 任务生成
python scripts/run.py submit   --study ... --real --confirm             # 提交（真实模式）
python scripts/run.py cycle    --study ... --real --confirm             # 一次检查周期
python scripts/run.py run      --study ... --real --confirm             # 循环到全部下载完成
python scripts/run.py check                                              # 数据质量检查
python scripts/run.py prepare  --study ...                               # 数据整理
python scripts/run.py options                                            # 推荐分析/绘图方案
python scripts/run.py plot     --select 5,11,21                          # 绘图 + 自动读图
python scripts/run.py report                                             # 生成最终报告
python scripts/run.py demo     --study examples/study_ngari_2022.json   # 模拟全流程（不联网）
python tests/run_all.py                                                  # 运行测试
```

> 不加 `--real` 时**一律为模拟模式**，不访问任何真实 API。

---

## 23. 工作区与数据隔离

- 默认工作区：`F:/skills/atmospheric-data-research/workspace`（Skill 自包含）。
- **本 Skill 不修改、不删除任何外部项目的数据。**
- 需要复用已有数据时：把需要的文件**复制**到工作区，或把 `workspace.raw_dir` 指向只读的外部目录
  （Skill 对 raw 永远只读）。
