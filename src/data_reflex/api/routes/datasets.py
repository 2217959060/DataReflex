# -*- coding: utf-8 -*-
"""数据集导入 API：接收 CSV/Excel 上传并调用 DataImporter。

上游：前端 multipart 上传。
本文件：落临时文件 → ``importer.import_files`` → 返回 dataset_id / schema。
下游：后续任务用 dataset_id 绑定沙箱可读数据。

前缀：``/api/datasets``。
"""

from __future__ import annotations

import shutil
import uuid
from pathlib import Path
from typing import Annotated

from fastapi import APIRouter, Depends, File, UploadFile

from data_reflex.api.deps import AppRuntime, get_runtime
from data_reflex.exceptions import DataImportError
from data_reflex.models.schemas import DataImportReq, DataImportResp

router = APIRouter(prefix="/api/datasets", tags=["datasets"])


@router.post("/import", response_model=DataImportResp)
async def import_datasets(
    files: Annotated[list[UploadFile], File(description="CSV/Excel uploads")],
    runtime: Annotated[AppRuntime, Depends(get_runtime)],
) -> DataImportResp:
    """接收上传、落盘临时目录，再跑 DataImporter。"""
    if not files:
        raise DataImportError("at least one file is required")

    batch_dir = runtime.upload_dir / str(uuid.uuid4())
    batch_dir.mkdir(parents=True, exist_ok=True)
    saved: list[Path] = []
    try:
        for upload in files:
            name = Path(upload.filename or "upload.bin").name
            dest = batch_dir / name
            with dest.open("wb") as out:
                shutil.copyfileobj(upload.file, out)
            saved.append(dest)

        resp = runtime.importer.import_files(
            DataImportReq(file_paths=[str(p) for p in saved])
        )
        if not resp.success:
            raise DataImportError(resp.error_msg or "import failed")
        return resp
    finally:
        shutil.rmtree(batch_dir, ignore_errors=True)
