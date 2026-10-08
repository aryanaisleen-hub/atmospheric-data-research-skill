# {{ title }}

> 生成时间：{{ generated_at }}　|　运行模式：{{ mode }}

## 1. 研究任务
- 任务标题：{{ study.title }}
- 科学问题：{{ study.research_question }}
- 观测变量：{{ study.station_variables | join(", ") }}

## 2. 时间与空间范围
- 时间范围：{{ study.start }} 至 {{ study.end }}（UTC）
- 空间范围：{{ study.region.north }}°N – {{ study.region.south }}°N，{{ study.region.west }}°E – {{ study.region.east }}°E
- 观测站：{{ study.station.name }}（{{ study.station.latitude }}°N, {{ study.station.longitude }}°E）

## 3. 数据来源与下载情况
- ERA5：Copernicus Climate Data Store（CDS）
- FIRMS：NASA FIRMS Area API
- 任务总数：{{ queue.total }}；状态分布：`{{ queue.states }}`
- 并发设置：首次提交 {{ queue.initial_submit }} 个，上限 {{ queue.max_concurrent }} 个

## 4. 数据质量检查
{% for row in quality %} - [{{ row.status }}] {{ row.task_id }}：{{ row.notes }}
{% endfor %}

## 5. 分析方法
{% for key, desc in methods.items() %} - {{ key }}：{{ desc }}
{% endfor %}

## 6. 图形列表与逐图解释
{% for figure in figures %}
### 图 {{ loop.index }}：{{ figure.title }}
![{{ figure.title }}]({{ figure.png }})

**图展示了什么**：{{ figure.caption }}

**主要现象**：
{% for item in figure.findings %} - {{ item }}
{% endfor %}
**与研究问题的关系**：{{ figure.link }}

**科学解释（谨慎）**：{{ figure.science }}

{% endfor %}
## 7. 综合科学解释
{{ synthesis }}

## 8. 不确定性
{{ uncertainty }}

## 9. 后续建议
{{ next_steps }}

---
_本报告由 atmospheric-data-research Skill 自动生成。_
