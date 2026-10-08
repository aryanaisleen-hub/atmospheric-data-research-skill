"""编排器：串联完整科研数据生命周期。

需求解析 → 数据判断 → MAP_KEY 检查 → 任务生成 → 首次提交 3 个 → 定时检查 →
下载 → 释放名额并补充提交（上限 5） → 数据质检 → 整理 → 方案推荐 → 绘图 →
自动读图 → 逐图解释 → 最终报告。

支持断点续传：每次运行都从 logs/data_requests.json 恢复，不重复提交、不重复下载。
"""

from __future__ import annotations

import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from .analysis.options import available_options, plan_from_selection, recommend_options, render_menu
from .data_check.checks import check_all, summarize, write_quality_report
from .era5.client import RealEra5Backend, monthly_target
from .firms.client import RealFirmsBackend, check_map_key, firms_target
from .planning import StudySpec, build_spec, describe_plan, plan_tasks
from .plotting.figures import RenderContext, render_option
from .plotting.review import review_and_fix
from .prepare import prepare_all, save_processed
from .queue import mark_initial_batch, plan_submissions, queue_status
from .report import ReportInputs, build_report, write_report
from .settings import Region, Settings
from .simulate import make_simulated_backends
from .states import TaskState, map_provider_status
from .store import Task, TaskStore


class Orchestrator:
    def __init__(
        self,
        settings: Settings,
        *,
        mode: str = "simulate",
        backend: dict[str, Any] | None = None,
        station_path: Path | None = None,
        logger: Callable[[str], None] | None = None,
    ) -> None:
        self.settings = settings
        self.mode = mode
        self.settings.ensure_dirs()
        self.station_path = station_path
        self._log_lines: list[str] = []
        self.log = logger or self._default_log
        if backend is not None:
            self.backend = backend
        elif mode == "simulate":
            self.backend = make_simulated_backends(settings)
        else:
            self.backend = {}
        self.store = TaskStore(settings.requests_file)
        self.spec: StudySpec | None = None

    # ---- 日志 ----
    def _default_log(self, message: str) -> None:
        line = f"{datetime.now(timezone.utc).isoformat()} {message}"
        self._log_lines.append(line)
        print(line, flush=True)

    def _write_log(self) -> None:
        path = self.settings.logs_dir / "orchestrator.log"
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as handle:
            handle.write("\n".join(self._log_lines) + "\n")

    # ---- 阶段 1：需求解析与任务生成 ----
    def initialize(
        self,
        *,
        title: str,
        start: str,
        end: str,
        region: Region,
        station: dict[str, Any] | None = None,
        research_question: str = "",
        station_variables: list[str] | None = None,
        **overrides: Any,
    ) -> StudySpec:
        """解析需求、生成任务并写入任务记录（断点续传入口）。"""
        self.store.load()
        spec = build_spec(
            title=title, start=start, end=end, region=region, station=station,
            research_question=research_question, settings=self.settings, **overrides,
        )
        if station_variables:
            spec.station_variables = list(station_variables)
        self.spec = spec

        existing = {task.task_id for task in self.store.tasks}
        new_tasks = [task for task in plan_tasks(spec, self.settings) if task.task_id not in existing]
        if new_tasks:
            self.store.add_many(new_tasks)
        self.store.data["study"] = spec.as_dict()
        self.store.save()
        self.log(f"[plan] 生成 {len(new_tasks)} 个新任务，记录中共 {len(self.store.tasks)} 个任务（断点续传已生效）")
        return spec

    def describe_plan(self) -> str:
        assert self.spec is not None
        return describe_plan(self.spec, self.store.tasks)

    # ---- 阶段 2：环境与凭据检查 ----
    def environment_report(self) -> dict[str, Any]:
        assert self.spec is not None
        report: dict[str, Any] = {"mode": self.mode, "firms_map_key": None, "cds": None}
        if self.spec.needs_fire:
            ready, message = check_map_key(self.settings)
            report["firms_map_key"] = {"ready": ready, "message": message}
            self.log(f"[env] FIRMS MAP_KEY：{'已就绪' if ready else '未配置'}")
        report["cds"] = {
            "ready": self.mode == "simulate" or (Path.home() / ".cdsapirc").exists(),
            "message": "模拟模式无需 CDS 凭据。" if self.mode == "simulate"
            else f"CDS 配置文件：{Path.home() / '.cdsapirc'}",
        }
        return report

    def _firms_available(self) -> bool:
        if self.mode == "simulate":
            return True
        return check_map_key(self.settings)[0]

    def _ensure_backend(self) -> None:
        """按需懒加载真实后端。"""
        if self.mode == "simulate":
            return
        if "era5" not in self.backend and self.spec and (self.spec.needs_era5_single or self.spec.needs_era5_pressure):
            self.backend["era5"] = RealEra5Backend(self.settings)
        if "firms" not in self.backend and self.spec and self.spec.needs_fire and self._firms_available():
            self.backend["firms"] = RealFirmsBackend(self.settings)

    # ---- 阶段 3：提交 ----
    def submit_pending(self) -> list[str]:
        assert self.spec is not None
        self._ensure_backend()
        plan = plan_submissions(self.store, self.settings)
        submitted: list[str] = []
        firms_ready = self._firms_available()
        for task in plan.to_submit:
            if task.kind == "firms" and not firms_ready:
                self.log(f"[skip] {task.task_id}：FIRMS MAP_KEY 未配置，暂停 FIRMS 下载流程")
                continue
            backend = self.backend.get("era5" if task.kind.startswith("era5") else "firms")
            if backend is None:
                self.log(f"[skip] {task.task_id}：后端未就绪")
                continue
            try:
                request_id, status = backend.submit(task)
            except Exception as exc:  # noqa: BLE001
                task.attempts += 1
                task.error_message = f"{type(exc).__name__}: {exc}"
                task.set_state(TaskState.FAILED, note="提交失败")
                self.store.update(task)
                self.log(f"[fail] 提交失败 {task.task_id}：{task.error_message}")
                continue
            task.request_id = task.request_id or request_id
            task.active_request_id = request_id
            task.provider_status = status
            task.submitted_at = datetime.now(timezone.utc).isoformat()
            task.attempts += 1
            target = map_provider_status(status) or TaskState.SUBMITTED
            task.set_state(TaskState.SUBMITTED, note=f"提交，provider={status}")
            if target is not TaskState.SUBMITTED:
                task.set_state(target, note=f"provider 直接返回 {status}")
            self.store.update(task)
            submitted.append(task.task_id)
            self.log(f"[submit] {task.task_id} id={request_id} provider={status} -> {task.status}")
        if submitted:
            mark_initial_batch(self.store)
            self.store.save()
        return submitted

    # ---- 阶段 4：定时检查 + 下载 ----
    def poll_and_download(self) -> dict[str, Any]:
        assert self.spec is not None
        self._ensure_backend()
        downloaded: list[str] = []
        changed: list[str] = []
        for task in self.store.active_tasks():
            backend = self.backend.get("era5" if task.kind.startswith("era5") else "firms")
            if backend is None:
                continue
            try:
                status = backend.status(task)
            except Exception as exc:  # noqa: BLE001
                self.log(f"[check] {task.task_id} 状态查询异常（保持原状态）：{type(exc).__name__}")
                continue
            task.provider_status = status
            task.last_checked = datetime.now(timezone.utc).isoformat()
            target_state = map_provider_status(status)
            if target_state is not None and target_state is not task.state:
                task.set_state(target_state, note=f"provider={status}")
                changed.append(task.task_id)
            self.store.update(task)

        # 下载已完成的任务
        for task in self.store.ready_to_download():
            backend = self.backend.get("era5" if task.kind.startswith("era5") else "firms")
            if backend is None:
                continue
            target = monthly_target(task, self.settings) if task.kind.startswith("era5") else firms_target(task, self.settings)
            if target.exists():
                task.set_state(TaskState.DOWNLOADED, note="文件已存在，跳过下载")
                task.downloaded = True
                task.local_path = str(target)
                self.store.update(task)
                continue
            task.set_state(TaskState.DOWNLOADING, note="开始下载")
            self.store.update(task)
            try:
                backend.download(task, target)
            except Exception as exc:  # noqa: BLE001
                task.error_message = f"{type(exc).__name__}: {exc}"
                task.set_state(TaskState.FAILED, note="下载失败")
                if task.attempts < int(self.settings.download.get("max_retries", 3)):
                    task.set_state(TaskState.RETRY, note="标记重试")
                self.store.update(task)
                self.log(f"[fail] 下载失败 {task.task_id}：{task.error_message}")
                continue
            task.downloaded = True
            task.local_path = str(target)
            task.error_message = None
            task.set_state(TaskState.DOWNLOADED, note="下载并校验通过")
            self.store.update(task)
            downloaded.append(task.task_id)
            self.log(f"[download] {task.task_id} -> {target}")

        self.store.save()
        return {"changed": changed, "downloaded": downloaded, "queue": queue_status(self.store, self.settings)}

    def cycle(self) -> dict[str, Any]:
        """一个完整的检查周期：先检查/下载，再补充提交。"""
        result = self.poll_and_download()
        submitted = self.submit_pending()
        result["submitted"] = submitted
        result["queue"] = queue_status(self.store, self.settings)
        self.log(f"[cycle] 下载 {len(result['downloaded'])} 个，提交 {len(submitted)} 个；{result['queue']}")
        self._write_log()
        return result

    def run_until_complete(self, *, max_cycles: int = 30, sleep_seconds: float = 0.0) -> list[dict[str, Any]]:
        """循环直到全部下载完成或达到最大周期数。"""
        history: list[dict[str, Any]] = []
        for _ in range(max_cycles):
            result = self.cycle()
            history.append(result)
            if self.store.all_downloaded():
                self.log("[done] 所有数据均已下载完成")
                break
            if (
                not self.store.active_tasks()
                and not self.store.submittable_tasks()
                and not self.store.ready_to_download()
            ):
                self.log("[stop] 没有进行中任务，也没有可提交任务")
                break
            if sleep_seconds:
                time.sleep(sleep_seconds)
        return history

    # ---- 阶段 5：质检与整理 ----
    def data_check(self) -> dict[str, Any]:
        rows = check_all(self.store, self.settings)
        path = write_quality_report(rows, self.settings)
        summary = summarize(rows)
        summary["report"] = str(path)
        self.log(f"[qc] 质检完成：{summary['pass']} 通过 / {summary['warn']} 警告 / {summary['fail']} 失败 -> {path}")
        return {"rows": rows, "summary": summary, "path": str(path)}

    def prepare(self) -> dict[str, Any]:
        assert self.spec is not None
        prepared = prepare_all(self.store.tasks, self.settings, self.spec, station_path=self.station_path)
        outputs = save_processed(prepared, self.settings)
        self.prepared = prepared
        self.log(f"[prepare] 整理完成，输出 {len(outputs)} 个文件；数据能力：{sorted(prepared.capabilities)}")
        return {"prepared": prepared, "outputs": {k: str(v) for k, v in outputs.items()}}

    # ---- 阶段 6：方案推荐 ----
    def recommend(self, top: int = 5) -> dict[str, Any]:
        assert self.spec is not None
        options = available_options(self.prepared, self.settings)
        recommended = recommend_options(self.prepared, self.spec, self.settings, top=top)
        menu = render_menu(options, self.settings, {option["id"] for option in recommended})
        self.log(f"[options] 可用方案 {len(options)} 个，推荐 {[o['id'] for o in recommended]}")
        return {"options": options, "recommended": recommended, "menu": menu}

    # ---- 阶段 7：绘图 + 自动读图 ----
    def render_figures(self, selection: list[int], options: list[dict[str, Any]] | None = None) -> list[Any]:
        assert self.spec is not None
        options = options or available_options(self.prepared, self.settings)
        chosen = plan_from_selection(selection, options)
        results = []
        for index, option in enumerate(chosen, 1):
            ctx = RenderContext(
                prepared=self.prepared, spec=self.spec, settings=self.settings,
                tasks=self.store.tasks, figures_dir=self.settings.figures_dir,
                tables_dir=self.settings.tables_dir, index=index,
            )
            result = render_option(ctx, option)
            result = review_and_fix(ctx, option, result, self.settings)
            results.append(result)
            self.log(f"[figure] 图 {index} {result.title} -> {result.png}（审查：{result.status}）")
        self._write_log()
        return results

    # ---- 阶段 8：报告 ----
    def build_report(self, figures: list[Any], quality: dict[str, Any], mode: str | None = None) -> Path:
        assert self.spec is not None
        inputs = ReportInputs(
            spec=self.spec,
            prepared=self.prepared,
            quality_rows=quality.get("rows", []),
            figures=figures,
            queue_summary={
                "total": len(self.store.tasks),
                **queue_status(self.store, self.settings),
            },
            mode=mode or self.mode,
        )
        text = build_report(inputs, self.settings)
        path = write_report(text, self.settings)
        self.log(f"[report] 报告已生成：{path}")
        self._write_log()
        return path

    # ---- 一键全流程 ----
    def run_full(self, selection: list[int] | None = None, *, max_cycles: int = 30) -> dict[str, Any]:
        assert self.spec is not None
        self.run_until_complete(max_cycles=max_cycles)
        quality = self.data_check()
        prepared = self.prepare()
        recommendation = self.recommend()
        selection = selection or [option["id"] for option in recommendation["recommended"]]
        figures = self.render_figures(selection, recommendation["options"])
        report = self.build_report(figures, quality)
        return {
            "quality": quality,
            "prepared": prepared,
            "recommendation": recommendation,
            "figures": figures,
            "report": str(report),
            "queue": queue_status(self.store, self.settings),
        }
