# -*- coding: utf-8 -*-
"""共享 Pydantic 基类：禁止未知字段（``extra='forbid'``）。"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict


class StrictModel(BaseModel):
    """领域模型基类：拒收未声明字段，并自动 strip 字符串空白。"""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
