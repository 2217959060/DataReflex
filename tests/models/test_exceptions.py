# -*- coding: utf-8 -*-
from __future__ import annotations

import pytest

from data_reflex.exceptions import (
    ContextManageError,
    DataAgentError,
    DataImportError,
    DataReflexError,
    ExecutionTimeoutError,
    IntentParseError,
    OpGenerateError,
    PlanBuildError,
    PlanValidateError,
    ReportBuildError,
    ResourceLimitError,
    ResultValidateError,
    SafetyViolationError,
    SandboxUnavailableError,
    SchemaParseError,
    SelfHealMeltedError,
    TaskQueueError,
)

ALL_ERRORS: list[type[BaseException]] = [
    DataReflexError,
    DataAgentError,
    DataImportError,
    SchemaParseError,
    IntentParseError,
    PlanBuildError,
    PlanValidateError,
    OpGenerateError,
    SafetyViolationError,
    SandboxUnavailableError,
    ExecutionTimeoutError,
    ResourceLimitError,
    SelfHealMeltedError,
    ResultValidateError,
    ReportBuildError,
    ContextManageError,
    TaskQueueError,
]


@pytest.mark.parametrize("exc_cls", ALL_ERRORS)
def test_exception_name_ends_with_error(exc_cls: type[BaseException]) -> None:
    assert exc_cls.__name__.endswith("Error")


def test_data_agent_error_is_alias_of_data_reflex_error() -> None:
    assert DataAgentError is DataReflexError


@pytest.mark.parametrize(
    "exc_cls",
    [
        DataImportError,
        SchemaParseError,
        IntentParseError,
        PlanBuildError,
        PlanValidateError,
        OpGenerateError,
        SafetyViolationError,
        SandboxUnavailableError,
        ExecutionTimeoutError,
        ResourceLimitError,
        SelfHealMeltedError,
        ResultValidateError,
        ReportBuildError,
        ContextManageError,
        TaskQueueError,
    ],
)
def test_raise_from_preserves_cause(exc_cls: type[DataReflexError]) -> None:
    root = ValueError("root cause")
    with pytest.raises(exc_cls) as captured:
        try:
            raise root
        except ValueError as e:
            raise exc_cls("wrapped") from e
    assert captured.value.__cause__ is root
    assert isinstance(captured.value, DataReflexError)
