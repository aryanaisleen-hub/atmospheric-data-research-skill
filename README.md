# atmospheric-data-research

面向 AI/Codex 的**大气环境科研数据工作流 Skill**。

它把科研数据流程串成一条可断点续传、可审计、带用户确认节点的流水线：

> 需求解析 → 数据判断 → 申请 → 定时检查 → 下载 → 质检 → 分析 → 绘图 → 自动读图 → 解释 → 报告

支持的数据源：**ERA5**（CDS 单层/气压层）与 **NASA FIRMS**（VIIRS / MODIS 火点），
外加用户自带的站点观测（如阿里站 PM1）。

---

## 亮点

| 能力 | 说明 |
|---|---|
| **并发调度** | 首次最多提交 **3** 个申请；任意时刻进行中任务 **≤ 5**；完成即释放名额并补充提交 |
| **断点续传** | 状态记录在 `logs/data_requests.json`，重启后不重复提交、不重复下载 |
| **状态机** | 9 个明确状态 + 合法迁移校验 + 每次变化写日志；禁止模糊状态 |
| **数据质检** | ERA5 11 项 + FIRMS 6 项检查，输出 `data_quality_report.csv` |
| **方案推荐** | 依据「数据能力」从 A–F 共 26 项方案中推荐，而不是把图全画出来 |
| **论文级绘图** | matplotlib，300 dpi，PNG + PDF/SVG；无 cartopy 时用附带国界矢量降级 |
| **自动读图** | 实际打开 PNG 检查空白/裁切/内容/图例，发现问题自动修正最多 3 次 |
| **谨慎解释** | 逐图四层解释，使用「可能表明 / 与……一致」等措辞，不轻言因果 |
| **密钥安全** | KEY 只从环境变量读取，绝不写入代码/日志/报告 |
| **数据隔离** | 原始数据只读；不修改、不删除任何外部项目的数据 |

---

## 快速开始

### 1. 依赖

需要 Python 3.10+。依赖清单见 **`requirements.txt`**：

```bash
python -m pip install -r requirements.txt
```

（`cartopy` 可选——有则用其投影，无则降级为经纬度坐标轴；`cdsapi` 仅在真实 ERA5 下载时需要。）

### 2. 先跑模拟流程（不联网）

```bash
python examples/run_simulation.py
```

会用合成数据完整跑通「解析 → 申请 → 定时检查 → 下载 → 质检 → 推荐 → 绘图 → 读图 → 报告」，
并在 `examples/_runs/<时间戳>/` 下生成图件与报告。**不访问任何真实 API。**

### 3. 配置凭据（真实数据时）

**FIRMS MAP_KEY**（自行在 <https://firms.modaps.eosdis.nasa.gov/api/map_key/> 申请）：

```powershell
$env:FIRMS_MAP_KEY = "你收到的 MAP_KEY"
```

**CDS**：登录 Climate Data Store，按官方说明在用户主目录创建 `.cdsapirc`。

> KEY 只放在环境变量或 `.cdsapirc` 中，**不要**写进任何文件。

### 4. 真实运行

```bash
python scripts/run.py env    --study examples/study_ngari_2022.json
python scripts/run.py plan   --study examples/study_ngari_2022.json
python scripts/run.py run    --study examples/study_ngari_2022.json --real --confirm
python scripts/run.py check
python scripts/run.py prepare --study examples/study_ngari_2022.json
python scripts/run.py options
python scripts/run.py plot   --select 5,11,21
python scripts/run.py report
```

不加 `--real` 时**一律为模拟模式**。

---

## 自定义研究任务

新建一个 study JSON（见 `examples/study_ngari_2022.json`）：

```json
{
  "title": "研究标题",
  "start": "2022-06-01",
  "end": "2022-06-30",
  "region": {"north": 40, "south": 20, "west": 60, "east": 90},
  "station": {"name": "Ngari / Ali", "latitude": 33.39, "longitude": 79.70},
  "research_question": "研究……的影响/关系/输送条件",
  "station_variables": ["PM1"],
  "pressure_levels": [850, 700, 600, 500],
  "firms_products": ["VIIRS_SNPP_SP"]
}
```

`start`、`end`、`region` **必须提供**——缺失时 Skill 会要求你先补全，不会擅自猜测。

新增变量或数据产品：编辑 `config/datasets.yaml`（变量/气压层/产品均可扩展，无需改代码）。
新增绘图方案：编辑 `config/analysis_catalog.yaml` 并在 `scripts/research_kit/plotting/figures.py` 注册渲染函数。

---

## 目录结构

```
atmospheric-data-research/
├── SKILL.md                  AI 工作流说明（给 AI 读）
├── README.md                 本文件（给人读）
├── config/                   设置、数据目录、变量、分析方案
├── scripts/run.py            命令行入口
├── scripts/research_kit/     核心实现（状态机/队列/客户端/质检/分析/绘图/报告/编排）
├── assets/boundaries/        Natural Earth 110m 国界（地图背景）
├── templates/                报告与请求清单模板
├── tests/                    单元测试 + 端到端测试
└── examples/                 示例任务与端到端演示
```

---

## 输出位置

工作区默认 `workspace/`（可在 `config/settings.yaml` 修改）：

| 内容 | 路径 |
|---|---|
| 原始数据（只读） | `data/` |
| 整理后数据 | `processed/` |
| 分析结果与报告 | `results/` |
| 图件 | `outputs/figures/` |
| 表格 | `outputs/tables/` |
| 日志与任务记录 | `logs/` |

---

## 测试

```bash
python tests/run_all.py
```

覆盖：状态机合法/非法迁移、并发「首提 3 / 上限 5」、断点续传不重复、端到端全流程。

---

## 安全与数据保护

- API KEY 只从环境变量 / `.cdsapirc` 读取；日志与错误信息中只显示「凭据已隐藏」。
- 原始数据只读；脚本**不会自动删除任何文件**。
- 本 Skill 默认工作区自包含，**不修改、不删除任何外部项目的数据**。
