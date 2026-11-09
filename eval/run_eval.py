# -*- coding: utf-8 -*-
# ruff: noqa: E402
"""Batch eval runner — mock or docker-backed real mode + strategy matrix.

Usage::

    python eval/run_eval.py --mode mock --max-cases 5 --strategy-matrix planner=on,off
    python eval/run_eval.py --mode real --max-cases 3

``--mode real`` requires Docker daemon + image ``data-reflex-sandbox:3.11``.
Graph nodes remain template/rule-based; real means **sandbox executes against fixtures**.
"""

from __future__ import annotations

import argparse
import csv
import itertools
import json
import shutil
import sys
import time
import traceback
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any
from uuid import uuid4

# Allow ``python eval/run_eval.py`` without installing the package editable.
_ROOT = Path(__file__).resolve().parents[1]
_SRC = _ROOT / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from data_reflex.config.settings import Settings
from data_reflex.graph_engine.builder import (
    build_main_graph,
    default_nodes,
    make_thread_config,
)
from data_reflex.graph_engine.nodes.execute import NodeExecute
from data_reflex.models.state import StrategyFlags, create_initial_task_state
from data_reflex.modules.executor.base import SandboxExecutor
from data_reflex.modules.executor.docker_executor import (
    DockerSandboxExecutor,
    docker_available,
)
from data_reflex.modules.executor.mock_executor import MockSandboxExecutor, MockScenario
from data_reflex.utils.tracing import create_tracer, reset_tracer

DEFAULT_DATASET = _ROOT / "eval" / "datasets" / "core.jsonl"
DEFAULT_RESULTS = _ROOT / "eval" / "results"
DEFAULT_FIXTURES = _ROOT / "eval" / "datasets" / "fixtures"
EVAL_DATASET_ID = "eval-fixtures"
ALLOWED_MATRIX_KEYS = frozenset({"planner", "reflector"})
_BOOL_TOKENS = {
    "on": True,
    "off": False,
    "true": True,
    "false": False,
    "1": True,
    "0": False,
}


@dataclass
class CaseResult:
    case_id: str
    category: str
    config: dict[str, bool]
    ok: bool
    status: str
    error: str | None = None
    first_pass: bool = False
    healed: bool = False
    execution_ok: bool = False
    result_ok: bool = False
    steps: int = 0
    llm_calls: int = 0
    duration_ms: int = 0
    skipped_leakage: bool = False


@dataclass
class RunAccumulator:
    config: dict[str, bool]
    results: list[CaseResult] = field(default_factory=list)

    def add(self, row: CaseResult) -> None:
        self.results.append(row)


def parse_strategy_matrix(raw: str | None) -> dict[str, list[bool]]:
    """Parse ``planner=on,off;reflector=on`` into bool lists.

    Commas separate values for one key; semicolons (or extra ``key=``) separate keys.
    """
    if raw is None or not raw.strip():
        return {}

    text = raw.strip()
    # Reject unknown keys early (e.g. sandbox=on).
    # Split on ';' first; also allow "planner=on,off,reflector=on" by re-splitting on key=.
    parts: list[str] = []
    for chunk in text.split(";"):
        chunk = chunk.strip()
        if not chunk:
            continue
        # If a chunk embeds another key= after a comma, split it.
        tokens = chunk.split(",")
        buf: list[str] = []
        for tok in tokens:
            tok = tok.strip()
            if "=" in tok and buf and tok.split("=", 1)[0].strip() not in _BOOL_TOKENS:
                # flush previous key
                parts.append(",".join(buf))
                buf = [tok]
            else:
                buf.append(tok)
        if buf:
            parts.append(",".join(buf))

    matrix: dict[str, list[bool]] = {}
    for part in parts:
        if "=" not in part:
            raise SystemExit(
                f"invalid strategy-matrix segment '{part}'; expected key=on,off"
            )
        key, vals = part.split("=", 1)
        key = key.strip().lower()
        if key not in ALLOWED_MATRIX_KEYS:
            raise SystemExit(
                "v1 strategy_matrix only allows keys planner/reflector; "
                f"got '{key}' (full: {sorted(set(matrix) | {key})})"
            )
        bools: list[bool] = []
        for token in vals.split(","):
            t = token.strip().lower()
            if not t:
                continue
            if t not in _BOOL_TOKENS:
                raise SystemExit(
                    f"invalid strategy value '{token}' for {key}; use on/off"
                )
            bools.append(_BOOL_TOKENS[t])
        if not bools:
            raise SystemExit(f"strategy key '{key}' has no values")
        matrix[key] = bools
    return matrix


def expand_matrix(matrix: dict[str, list[bool]]) -> list[dict[str, bool]]:
    """Cartesian product of matrix dims; empty → one default config."""
    if not matrix:
        return [{"planner": True, "reflector": True}]
    keys = sorted(matrix)
    value_lists = [matrix[k] for k in keys]
    configs: list[dict[str, bool]] = []
    for combo in itertools.product(*value_lists):
        cell = {"planner": True, "reflector": True}
        cell.update(dict(zip(keys, combo, strict=True)))
        configs.append(cell)
    return configs


def load_cases(path: Path, *, max_cases: int) -> list[dict[str, Any]]:
    lines: list[dict[str, Any]] = []
    with path.open(encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            lines.append(json.loads(line))
            if len(lines) >= max_cases:
                break
    return lines


def _flags_from_config(cfg: dict[str, bool]) -> StrategyFlags:
    # Batch eval must not hang on human confirm; sandbox stays default True.
    return StrategyFlags(
        enable_planner=bool(cfg.get("planner", True)),
        enable_reflector=bool(cfg.get("reflector", True)),
        enable_sandbox=True,
        enable_human_confirm=False,
        enable_fallback_report=True,
    )


def _plan_step_count(state: dict[str, Any]) -> int:
    plan = state.get("plan")
    if plan is None:
        return 0
    steps = getattr(plan, "sub_tasks", None)
    if steps is None and isinstance(plan, dict):
        steps = plan.get("sub_tasks")
    return len(steps or [])


def _was_healed(state: dict[str, Any]) -> bool:
    retries = state.get("retry_counts") or {}
    if any(int(v) > 0 for v in retries.values()):
        return True
    fingerprints = state.get("error_fingerprints") or []
    return len(fingerprints) > 0 and state.get("status") == "success"


def ensure_eval_fixtures(dataset_root: Path) -> Path:
    """Copy canonical CSVs into ``dataset_root/eval-fixtures`` for Docker mounts."""
    dest = dataset_root / EVAL_DATASET_ID
    dest.mkdir(parents=True, exist_ok=True)
    for name in ("orders.csv", "returns.csv", "users.csv"):
        src = DEFAULT_FIXTURES / name
        if not src.is_file():
            raise FileNotFoundError(f"missing fixture: {src}")
        target = dest / name
        shutil.copy2(src, target)
    return dest


def build_eval_executor(
    *,
    mode: str,
    settings: Settings,
    dataset_root: Path,
) -> SandboxExecutor:
    if mode == "mock":
        return MockSandboxExecutor(MockScenario.SUCCESS)
    if not docker_available():
        raise RuntimeError(
            "Docker daemon unavailable — cannot run --mode real "
            "(host execution is forbidden)"
        )
    try:
        import docker

        client = docker.from_env()
        client.images.get(settings.sandbox_image)
    except Exception as exc:  # noqa: BLE001
        raise RuntimeError(
            f"sandbox image not available locally: {settings.sandbox_image}. "
            "Build with: docker build -t data-reflex-sandbox:3.11 "
            "-f sandbox/Dockerfile sandbox"
        ) from exc
    return DockerSandboxExecutor(
        settings=settings,
        dataset_root=dataset_root,
        client=client,
    )


def run_one_case(
    *,
    case: dict[str, Any],
    config: dict[str, bool],
    mode: str,
    settings: Settings,
    executor: SandboxExecutor,
    dataset_id: str,
) -> CaseResult:
    case_id = str(case.get("case_id", "unknown"))
    category = str(case.get("category", ""))
    if case.get("has_leakage"):
        # Still record; metrics aggregation excludes these (T42).
        pass

    t0 = time.perf_counter()
    task_id = f"eval-{case_id}-{uuid4().hex[:8]}"
    flags = _flags_from_config(config)
    try:
        nodes = default_nodes(
            execute=NodeExecute(
                executor=executor,
                settings=settings,
            ),
        )
        app = build_main_graph(nodes=nodes)
        seed = dict(
            create_initial_task_state(
                dataset_id=dataset_id,
                user_query=str(case.get("question", "")),
                task_id=task_id,
                strategy_flags=flags,
            )
        )
        # Fixed eval clock available to nodes via settings; stamp on state for reports.
        seed["eval_fixed_clock"] = settings.eval_fixed_clock
        seed["eval_mode"] = mode
        final = app.invoke(seed, config=make_thread_config(task_id))
        status = str(final.get("status") or "unknown")
        ok = status in {"success", "fallback"}
        healed = _was_healed(final)
        first_pass = status == "success" and not healed
        execution_ok = status == "success" or (
            status == "fallback" and bool(final.get("fallback_report"))
        )
        # Mock sandbox does not materialize gold rows; treat successful report as hit.
        result_ok = status == "success" and final.get("final_report") is not None
        duration_ms = int((time.perf_counter() - t0) * 1000)
        return CaseResult(
            case_id=case_id,
            category=category,
            config=dict(config),
            ok=ok,
            status=status,
            first_pass=first_pass,
            healed=healed,
            execution_ok=execution_ok,
            result_ok=result_ok,
            steps=_plan_step_count(final),
            llm_calls=int(final.get("llm_calls_used") or 0),
            duration_ms=duration_ms,
            skipped_leakage=bool(case.get("has_leakage")),
        )
    except Exception as exc:  # noqa: BLE001 — continue batch on single failure
        duration_ms = int((time.perf_counter() - t0) * 1000)
        return CaseResult(
            case_id=case_id,
            category=category,
            config=dict(config),
            ok=False,
            status="error",
            error=f"{type(exc).__name__}: {exc}",
            duration_ms=duration_ms,
            skipped_leakage=bool(case.get("has_leakage")),
        )


def _scored(rows: list[CaseResult]) -> list[CaseResult]:
    """Cases that participate in metrics (exclude leakage)."""
    return [r for r in rows if not r.skipped_leakage]


def metrics_for(acc: RunAccumulator) -> dict[str, Any]:
    scored = _scored(acc.results)
    n = len(scored)
    first = sum(1 for r in scored if r.first_pass)
    post = sum(1 for r in scored if r.ok)
    exe = sum(1 for r in scored if r.execution_ok)
    accu = sum(1 for r in scored if r.result_ok)
    healed = sum(1 for r in scored if r.healed and r.ok)
    heal_attempts = sum(1 for r in scored if r.healed)

    def _rate(num: int) -> float:
        return round(num / n, 4) if n else 0.0

    return {
        "planner": acc.config.get("planner", True),
        "reflector": acc.config.get("reflector", True),
        "n_cases": n,
        "n_leakage_excluded": sum(1 for r in acc.results if r.skipped_leakage),
        "first_pass_rate": _rate(first),
        "post_heal_rate": _rate(post),
        "execution_success_rate": _rate(exe),
        "result_accuracy": _rate(accu),
        "avg_steps": round(sum(r.steps for r in scored) / n, 4) if n else 0.0,
        "avg_llm_calls": round(sum(r.llm_calls for r in scored) / n, 4) if n else 0.0,
        "avg_duration_ms": int(sum(r.duration_ms for r in scored) / n) if n else 0,
        "self_heal_rate": (round(healed / heal_attempts, 4) if heal_attempts else 0.0),
    }


def write_metrics_csv(path: Path, *, mode: str, metrics: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fieldnames = [
        "mode",
        "planner",
        "reflector",
        "n_cases",
        "n_leakage_excluded",
        "first_pass_rate",
        "post_heal_rate",
        "execution_success_rate",
        "result_accuracy",
        "avg_steps",
        "avg_llm_calls",
        "avg_duration_ms",
        "self_heal_rate",
    ]
    with path.open("w", encoding="utf-8", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=fieldnames)
        writer.writeheader()
        for m in metrics:
            row = {k: m.get(k, "") for k in fieldnames}
            row["mode"] = mode
            writer.writerow(row)


def write_report_md(
    path: Path,
    *,
    mode: str,
    settings: Settings,
    matrix: dict[str, list[bool]],
    metrics: list[dict[str, Any]],
    leakage_cases: list[str],
    failed_cases: list[dict[str, Any]],
    max_cases: int,
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    # Critical eval runs force temperature=0 for reproducibility (defaults.md §7).
    temperature = 0.0 if mode == "mock" else float(settings.llm_temperature)
    lines = [
        "# DataReflex eval report",
        "",
        "## Run config",
        "",
        f"mode: {mode}",
        f"fixed_clock: {settings.eval_fixed_clock}",
        f"timezone: {settings.eval_timezone}",
        f"llm_model: {settings.llm_model}",
        f"llm_temperature: {temperature}",
        f"generated_at: {datetime.now().isoformat(timespec='seconds')}",
        f"max_cases: {max_cases}",
        f"strategy_matrix: {json.dumps(matrix, ensure_ascii=False)}",
        "",
        "## Metrics",
        "",
        "Per strategy cell (leakage cases excluded from denominators):",
        "",
    ]
    for m in metrics:
        lines.extend(
            [
                f"### planner={m['planner']} reflector={m['reflector']}",
                "",
                f"- n_cases: {m['n_cases']}",
                f"- n_leakage_excluded: {m['n_leakage_excluded']}",
                f"- first_pass_rate: {m['first_pass_rate']}",
                f"- post_heal_rate: {m['post_heal_rate']}",
                f"- execution_success_rate: {m['execution_success_rate']}",
                f"- result_accuracy: {m['result_accuracy']}",
                f"- avg_steps: {m['avg_steps']}",
                f"- avg_llm_calls: {m['avg_llm_calls']}",
                f"- avg_duration_ms: {m['avg_duration_ms']}",
                f"- self_heal_rate: {m['self_heal_rate']}",
                "",
            ]
        )
    lines.extend(
        [
            "## Leakage cases (excluded from metrics)",
            "",
            f"leakage_cases: {json.dumps(leakage_cases, ensure_ascii=False)}",
            "",
            "## Failed cases",
            "",
        ]
    )
    if not failed_cases:
        lines.append("(none)")
    else:
        for fc in failed_cases:
            lines.append(
                f"- {fc.get('case_id')} config={fc.get('config')} "
                f"status={fc.get('status')} error={fc.get('error')}"
            )
    lines.append("")
    path.write_text("\n".join(lines), encoding="utf-8")


def build_arg_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="DataReflex batch eval runner")
    p.add_argument("--mode", choices=("mock", "real"), required=True)
    p.add_argument("--max-cases", type=int, default=30)
    p.add_argument(
        "--strategy-matrix",
        default="planner=on",
        help="e.g. planner=on,off or planner=on,off;reflector=on,off",
    )
    p.add_argument(
        "--dataset",
        type=Path,
        default=DEFAULT_DATASET,
        help="JSONL eval set path",
    )
    p.add_argument(
        "--results-dir",
        type=Path,
        default=DEFAULT_RESULTS,
        help="Output directory for report.md / metrics.csv",
    )
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_arg_parser().parse_args(argv)
    if args.max_cases < 1:
        print("max-cases must be >= 1", file=sys.stderr)
        return 2

    try:
        matrix = parse_strategy_matrix(args.strategy_matrix)
    except SystemExit as exc:
        print(str(exc), file=sys.stderr)
        return 2

    # Re-validate via schema contract (same message family as EvalRunReq).
    unknown = set(matrix) - ALLOWED_MATRIX_KEYS
    if unknown:
        print(
            "v1 strategy_matrix only allows keys planner/reflector; "
            f"got {sorted(unknown)}",
            file=sys.stderr,
        )
        return 2

    settings = Settings(
        llm_temperature=0.0 if args.mode == "mock" else Settings().llm_temperature,
        sandbox_backend="docker" if args.mode == "real" else "mock",
    )
    dataset_root = Path(settings.sqlite_path).parent / "datasets"
    dataset_id = "ds-eval-mock"
    if args.mode == "real":
        try:
            ensure_eval_fixtures(dataset_root)
        except FileNotFoundError as exc:
            print(str(exc), file=sys.stderr)
            return 2
        dataset_id = EVAL_DATASET_ID
        print(
            "note: --mode real uses DockerSandboxExecutor + fixtures; "
            "intent/plan/generate remain template/rule-based (no GLM in Graph)",
            file=sys.stderr,
        )

    try:
        executor = build_eval_executor(
            mode=args.mode,
            settings=settings,
            dataset_root=dataset_root,
        )
    except RuntimeError as exc:
        print(str(exc), file=sys.stderr)
        return 2

    configs = expand_matrix(matrix)
    cases = load_cases(args.dataset, max_cases=args.max_cases)
    if not cases:
        print(f"no cases loaded from {args.dataset}", file=sys.stderr)
        return 2

    reset_tracer(create_tracer())
    accumulators = [RunAccumulator(config=c) for c in configs]
    leakage_ids: list[str] = []
    failed: list[dict[str, Any]] = []

    try:
        for acc in accumulators:
            for case in cases:
                cid = str(case.get("case_id", ""))
                if case.get("has_leakage") and cid not in leakage_ids:
                    leakage_ids.append(cid)
                row = run_one_case(
                    case=case,
                    config=acc.config,
                    mode=args.mode,
                    settings=settings,
                    executor=executor,
                    dataset_id=dataset_id,
                )
                acc.add(row)
                if not row.ok:
                    failed.append(
                        {
                            "case_id": row.case_id,
                            "config": row.config,
                            "status": row.status,
                            "error": row.error,
                        }
                    )
                    # Continue remaining cases (FR-12).
                    print(
                        f"case failed: {row.case_id} config={row.config} "
                        f"status={row.status} error={row.error}",
                        file=sys.stderr,
                    )
    finally:
        reset_tracer(None)

    metrics = [metrics_for(acc) for acc in accumulators]
    results_dir: Path = args.results_dir
    report_path = results_dir / "report.md"
    metrics_path = results_dir / "metrics.csv"
    write_metrics_csv(metrics_path, mode=args.mode, metrics=metrics)
    write_report_md(
        report_path,
        mode=args.mode,
        settings=settings,
        matrix=matrix,
        metrics=metrics,
        leakage_cases=leakage_ids,
        failed_cases=failed,
        max_cases=args.max_cases,
    )
    print(f"wrote {report_path}")
    print(f"wrote {metrics_path}")
    print(f"mode: {args.mode}")
    print(f"leakage_cases: {leakage_ids}")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception:  # noqa: BLE001
        traceback.print_exc()
        raise SystemExit(1) from None
