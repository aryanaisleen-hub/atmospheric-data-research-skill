# 数据申请任务清单（模板）

> 本文件由 Skill 自动生成于 `logs/data_requests.json`。请勿手工编辑。
> 字段含义见 SKILL.md「任务状态机」一节。

```json
{
  "schema": 1,
  "created_at": "<ISO8601 UTC>",
  "updated_at": "<ISO8601 UTC>",
  "initial_batch_done": false,
  "study": {
    "title": "<研究任务标题>",
    "start": "<YYYY-MM-DD>",
    "end": "<YYYY-MM-DD>",
    "region": {"north": 0, "south": 0, "west": 0, "east": 0},
    "station": {"name": "", "latitude": 0, "longitude": 0},
    "station_variables": ["PM1"],
    "research_question": "<科学问题>"
  },
  "tasks": [
    {
      "task_id": "era5-single-2022-06",
      "kind": "era5_single",
      "label": "ERA5 单层 2022-06（6 变量）",
      "dataset": "reanalysis-era5-single-levels",
      "product": "",
      "year": 2022,
      "month": 6,
      "start_time": "2022-06-01T00:00:00",
      "end_time": "2022-06-30T23:00:00",
      "variables": ["2m_temperature", "total_precipitation", "boundary_layer_height"],
      "levels": [],
      "area": [40, 60, 20, 90],
      "status": "PENDING",
      "request_id": null,
      "active_request_id": null,
      "provider_status": null,
      "submitted_at": null,
      "last_checked": null,
      "downloaded": false,
      "local_path": null,
      "error_message": null,
      "attempts": 0,
      "history": []
    }
  ]
}
```

## 状态取值

| 状态 | 含义 |
|---|---|
| PENDING | 已生成、尚未提交 |
| SUBMITTED | 已提交给数据提供方 |
| QUEUED | 提供方排队中 |
| RUNNING | 提供方处理中 |
| COMPLETED | 提供方完成、尚未下载 |
| DOWNLOADING | 正在下载 |
| DOWNLOADED | 已下载并校验通过（终态） |
| FAILED | 失败 |
| RETRY | 标记为待重试 |

## 并发规则

- 首次提交：最多 3 个（`download.initial_submit`）。
- 任意时刻：SUBMITTED/QUEUED/RUNNING 合计不超过 5 个（`download.max_concurrent`）。
- 每当有任务下载完成释放名额，自动补充提交新任务。
