# -*- coding: utf-8 -*-
"""Docker 一次性沙箱执行器：生成代码只在容器内跑，宿主机禁止执行。

上游：``Node_Execute``（默认生产后端）。
本文件：限 CPU/内存/超时/无网络；解析结果标记；引擎不可用抛 ``SandboxUnavailableError``。
下游：``ExecuteResp`` → 结果校验或错误分类；不提供本机降级执行。

See ``tasks.md`` T13 / D-DOCKER-01。
"""

from __future__ import annotations

import contextlib
import json
import threading
import time
from pathlib import Path
from typing import Any

from data_reflex.config.settings import Settings, get_settings
from data_reflex.exceptions import SandboxUnavailableError
from data_reflex.models.schemas import ExecuteReq, ExecuteResp
from data_reflex.modules.executor.base import SandboxRunConfig
from data_reflex.utils.logger import get_logger, summarize_op

logger = get_logger(__name__)

_DEFAULT_PIDS_LIMIT = 64
_RESULT_MARKER = "__DATA_REFLEX_RESULT__"


def docker_available(client: Any | None = None) -> bool:
    """Return True when a Docker engine responds to ping."""
    try:
        c = client
        if c is None:
            import docker

            c = docker.from_env()
        c.ping()
        return True
    except Exception:  # noqa: BLE001 — availability probe must not raise
        return False


class DockerSandboxExecutor:
    """一次性容器执行 op；资源杀/超时写入 ExecuteResp，不回退宿主机。"""

    def __init__(
        self,
        *,
        settings: Settings | None = None,
        dataset_root: str | Path | None = None,
        client: Any | None = None,
        network_disabled: bool = True,
        pids_limit: int = _DEFAULT_PIDS_LIMIT,
        require_image_present: bool = True,
    ) -> None:
        self._settings = settings or get_settings()
        if dataset_root is None:
            dataset_root = Path(self._settings.sqlite_path).parent / "datasets"
        self._dataset_root = Path(dataset_root)
        self._client = client
        self._network_disabled = network_disabled
        self._pids_limit = pids_limit
        self._require_image_present = require_image_present
        self._lock = threading.RLock()
        self._active_container: Any | None = None
        self._terminated = False
        self._terminate_reason: str | None = None
        # Last kwargs passed to containers.run — asserted by unit tests.
        self.last_run_kwargs: dict[str, Any] | None = None
        self.last_run_config: SandboxRunConfig | None = None

    @property
    def image(self) -> str:
        return self._settings.sandbox_image

    @property
    def network_disabled(self) -> bool:
        return self._network_disabled

    @property
    def terminated(self) -> bool:
        return self._terminated

    def build_run_config(self, req: ExecuteReq) -> SandboxRunConfig:
        return SandboxRunConfig(
            timeout_seconds=req.timeout_seconds,
            memory_limit_mb=req.memory_limit_mb,
            cpu_limit=req.cpu_limit,
            network_disabled=self._network_disabled,
        )

    def terminate(self, reason: str = "") -> None:
        with self._lock:
            self._terminated = True
            self._terminate_reason = reason or "terminated"
            container = self._active_container
            self._active_container = None
        if container is not None:
            self._kill_and_remove(container)
            logger.info(
                "docker sandbox killed on terminate",
                extra={
                    "task_id": "-",
                    "dataset_id": "-",
                    "reason": self._terminate_reason,
                },
            )

    def execute(self, req: ExecuteReq) -> ExecuteResp:
        """Create a one-shot container; never eval/exec ``op_content`` on host."""
        if self._terminated:
            return ExecuteResp(
                success=False,
                stderr="sandbox terminated before execute",
                exit_code=137,
                resource_killed=False,
            )

        config = self.build_run_config(req)
        self.last_run_config = config
        client = self._require_client()
        self._ensure_image(client)

        logger.info(
            "docker sandbox execute",
            extra={
                "task_id": "-",
                "dataset_id": req.dataset_id,
                **summarize_op(req.op_type, req.op_content),
                "timeout_seconds": config.timeout_seconds,
                "memory_limit_mb": config.memory_limit_mb,
                "cpu_limit": config.cpu_limit,
                "network_disabled": config.network_disabled,
                # enable_sandbox may be False for eval — still one-shot container.
                "enable_sandbox": self._settings.enable_sandbox,
            },
        )

        work_dir = Path(self._dataset_root).parent / "_sandbox_work"
        work_dir.mkdir(parents=True, exist_ok=True)
        run_dir = work_dir / f"run_{int(time.time() * 1000)}_{threading.get_ident()}"
        run_dir.mkdir(parents=True, exist_ok=True)
        try:
            script_name = self._materialize_op(run_dir, req)
            volumes = self._build_volumes(run_dir, req.dataset_id)
            run_kwargs = self._build_container_kwargs(req, config, script_name, volumes)
            self.last_run_kwargs = dict(run_kwargs)

            started = time.perf_counter()
            try:
                container = client.containers.run(**run_kwargs)
            except SandboxUnavailableError:
                raise
            except Exception as exc:  # noqa: BLE001
                raise SandboxUnavailableError(
                    f"failed to start sandbox container: {type(exc).__name__}: {exc}"
                ) from exc

            with self._lock:
                self._active_container = container

            try:
                return self._await_container(container, config, started)
            finally:
                with self._lock:
                    self._active_container = None
                self._kill_and_remove(container)
        finally:
            # Best-effort cleanup of staged files (op text may be sensitive).
            try:
                for path in run_dir.glob("*"):
                    path.unlink(missing_ok=True)
                run_dir.rmdir()
            except OSError:
                pass

    # --- internals -------------------------------------------------------------

    def _require_client(self) -> Any:
        if self._client is not None:
            return self._client
        try:
            import docker

            client = docker.from_env()
            client.ping()
            return client
        except Exception as exc:  # noqa: BLE001
            raise SandboxUnavailableError(
                "Docker sandbox unavailable; host execution is forbidden"
            ) from exc

    def _ensure_image(self, client: Any) -> None:
        if not self._require_image_present:
            return
        image = self.image
        try:
            client.images.get(image)
        except Exception as exc:  # noqa: BLE001
            # Do not silently pull in unit tests; production may pre-load image.
            raise SandboxUnavailableError(
                f"sandbox image not available locally: {image}"
            ) from exc

    def _materialize_op(self, run_dir: Path, req: ExecuteReq) -> str:
        """Write op + in-container runner. Writing ≠ executing on host."""
        if req.op_type == "python":
            (run_dir / "op.py").write_text(req.op_content, encoding="utf-8")
            (run_dir / "py_runner.py").write_text(
                _python_runner_source(), encoding="utf-8"
            )
            return "py_runner.py"
        (run_dir / "op.sql").write_text(req.op_content, encoding="utf-8")
        (run_dir / "sql_runner.py").write_text(_sql_runner_source(), encoding="utf-8")
        return "sql_runner.py"

    def _build_volumes(
        self, run_dir: Path, dataset_id: str
    ) -> dict[str, dict[str, str]]:
        volumes: dict[str, dict[str, str]] = {
            str(run_dir.resolve()): {"bind": "/sandbox/work", "mode": "ro"},
        }
        data_dir = (self._dataset_root / dataset_id).resolve()
        if data_dir.is_dir():
            volumes[str(data_dir)] = {"bind": "/sandbox/data", "mode": "ro"}
        return volumes

    def _build_container_kwargs(
        self,
        req: ExecuteReq,
        config: SandboxRunConfig,
        script_name: str,
        volumes: dict[str, dict[str, str]],
    ) -> dict[str, Any]:
        command = ["python", f"/sandbox/work/{script_name}"]

        return {
            "image": self.image,
            "command": command,
            "volumes": volumes,
            "network_disabled": config.network_disabled,
            "mem_limit": f"{config.memory_limit_mb}m",
            "nano_cpus": int(config.cpu_limit * 1_000_000_000),
            "pids_limit": self._pids_limit,
            "detach": True,
            "stdout": True,
            "stderr": True,
            # One-shot: we always remove in finally; never leave orphans.
            "remove": False,
            "user": "nobody",
        }

    def _await_container(
        self,
        container: Any,
        config: SandboxRunConfig,
        started: float,
    ) -> ExecuteResp:
        try:
            wait_result = container.wait(timeout=config.timeout_seconds)
        except Exception as exc:  # noqa: BLE001 — timeout or API errors
            # Treat wait failures as timeout/resource kill when possible.
            self._kill_and_remove(container)
            elapsed = int((time.perf_counter() - started) * 1000)
            msg = str(exc).lower()
            if "timed out" in msg or "timeout" in msg or "read timed out" in msg:
                return ExecuteResp(
                    success=False,
                    stderr=f"execution timed out after {config.timeout_seconds}s",
                    exit_code=124,
                    duration_ms=elapsed,
                    resource_killed=True,
                )
            raise SandboxUnavailableError(
                f"sandbox wait failed: {type(exc).__name__}: {exc}"
            ) from exc

        elapsed = int((time.perf_counter() - started) * 1000)
        status = 0
        if isinstance(wait_result, dict):
            status = int(wait_result.get("StatusCode", 0))
        else:
            status = int(wait_result or 0)

        stdout, stderr = self._read_logs(container)
        if status == 137 or status == 139:
            return ExecuteResp(
                success=False,
                stdout=stdout,
                stderr=stderr or "resource limit killed process",
                exit_code=status,
                duration_ms=elapsed,
                resource_killed=True,
            )
        if status != 0:
            return ExecuteResp(
                success=False,
                stdout=stdout,
                stderr=stderr or f"container exit {status}",
                exit_code=status,
                duration_ms=elapsed,
                resource_killed=False,
            )

        rows, columns = self._parse_result(stdout, req_op_hint=None)
        return ExecuteResp(
            success=True,
            result_rows=rows,
            result_columns=columns,
            stdout=stdout[:2000],
            stderr=stderr[:2000],
            exit_code=0,
            duration_ms=elapsed,
            resource_killed=False,
        )

    def _read_logs(self, container: Any) -> tuple[str, str]:
        try:
            raw = container.logs(stdout=True, stderr=True)
        except Exception:  # noqa: BLE001
            return "", ""
        if isinstance(raw, bytes):
            text = raw.decode("utf-8", errors="replace")
        else:
            text = str(raw)
        # Docker multiplexes streams; keep a single blob split heuristically.
        return text, ""

    def _parse_result(
        self, stdout: str, req_op_hint: str | None
    ) -> tuple[list[dict[str, Any]], list[str]]:
        _ = req_op_hint
        for line in stdout.splitlines():
            if _RESULT_MARKER in line:
                payload = line.split(_RESULT_MARKER, 1)[1]
                try:
                    data = json.loads(payload)
                except json.JSONDecodeError:
                    continue
                columns = [str(c) for c in data.get("columns", [])]
                rows = list(data.get("rows", []))
                return rows, columns
        # Python scripts may print JSON directly.
        try:
            data = json.loads(stdout.strip().splitlines()[-1])
            if isinstance(data, dict) and "rows" in data:
                columns = [str(c) for c in data.get("columns", [])]
                return list(data.get("rows", [])), columns
        except (json.JSONDecodeError, IndexError, TypeError):
            pass
        return [], []

    @staticmethod
    def _kill_and_remove(container: Any) -> None:
        with contextlib.suppress(Exception):
            container.kill()
        with contextlib.suppress(Exception):
            container.remove(force=True)


def _sql_runner_source() -> str:
    """Stdlib runner: load ``/sandbox/data/*.csv`` then run ``op.sql``."""
    return (
        "import csv,json,pathlib,sqlite3,sys\n"
        f"marker={_RESULT_MARKER!r}\n"
        "def _load(con, root):\n"
        "    root=pathlib.Path(root)\n"
        "    if not root.is_dir():\n"
        "        return\n"
        "    for path in sorted(root.glob('*.csv')):\n"
        "        table=path.stem\n"
        "        with path.open(encoding='utf-8', newline='') as fh:\n"
        "            reader=csv.reader(fh)\n"
        "            try:\n"
        "                header=next(reader)\n"
        "            except StopIteration:\n"
        "                continue\n"
        "            cols=[str(c) for c in header]\n"
        "            if not cols:\n"
        "                continue\n"
        "            quoted=','.join('\"'+c.replace('\"','\"\"')+'\"' for c in cols)\n"
        "            con.execute(f'CREATE TABLE \"{table}\" ({quoted})')\n"
        "            placeholders=','.join('?' for _ in cols)\n"
        "            rows=[tuple(r) for r in reader if len(r)==len(cols)]\n"
        "            if rows:\n"
        "                con.executemany(\n"
        "                    f'INSERT INTO \"{table}\" VALUES ({placeholders})', rows\n"
        "                )\n"
        "sql=pathlib.Path('/sandbox/work/op.sql').read_text(encoding='utf-8')\n"
        "con=sqlite3.connect(':memory:')\n"
        "try:\n"
        "    _load(con, '/sandbox/data')\n"
        "    cur=con.execute(sql)\n"
        "    cols=[d[0] for d in (cur.description or [])]\n"
        "    rows=[dict(zip(cols,r)) for r in cur.fetchall()]\n"
        "    sys.stdout.write(\n"
        "        marker+json.dumps({'columns':cols,'rows':rows},ensure_ascii=False)+'\\n'\n"
        "    )\n"
        "except Exception as e:\n"
        "    sys.stderr.write(str(e)+'\\n')\n"
        "    sys.exit(1)\n"
    )


def _python_runner_source() -> str:
    """Load CSVs as DataFrames, exec ``op.py``, emit ``result`` / last DataFrame."""
    return (
        "import json,pathlib,sys\n"
        "import pandas as pd\n"
        f"marker={_RESULT_MARKER!r}\n"
        "ns={}\n"
        "data=pathlib.Path('/sandbox/data')\n"
        "if data.is_dir():\n"
        "    for path in sorted(data.glob('*.csv')):\n"
        "        ns[path.stem]=pd.read_csv(path)\n"
        "code=pathlib.Path('/sandbox/work/op.py').read_text(encoding='utf-8')\n"
        "try:\n"
        "    exec(compile(code, 'op.py', 'exec'), ns, ns)\n"
        "    frame=ns.get('result')\n"
        "    if frame is None:\n"
        "        for value in reversed(list(ns.values())):\n"
        "            if isinstance(value, pd.DataFrame):\n"
        "                frame=value\n"
        "                break\n"
        "    if frame is None:\n"
        "        raise RuntimeError('python op did not produce a DataFrame named result')\n"
        "    if not isinstance(frame, pd.DataFrame):\n"
        "        frame=pd.DataFrame(frame)\n"
        "    cols=[str(c) for c in frame.columns.tolist()]\n"
        "    rows=json.loads(frame.to_json(orient='records', force_ascii=False))\n"
        "    sys.stdout.write(\n"
        "        marker+json.dumps({'columns':cols,'rows':rows},ensure_ascii=False)+'\\n'\n"
        "    )\n"
        "except Exception as e:\n"
        "    sys.stderr.write(str(e)+'\\n')\n"
        "    sys.exit(1)\n"
    )
