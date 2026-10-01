"""HTTP request contracts for the phase-1 AI-3.0 application."""

from __future__ import annotations

import math
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class ApiModel(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


ConditionScalar = str | bool | int | float | None
MAX_EXTRA_CONDITION_FIELDS = 32
MAX_EXTRA_CONDITION_KEY_LENGTH = 80
RESERVED_CONDITION_FIELD_NAMES = {
    "line",
    "device_id",
    "model_name",
    "reference",
    "load_value",
    "load_unit",
    "speed_ratio",
    "acquired_at",
    "extra_fields",
}


class Conditions(ApiModel):
    line: str | None = None
    device_id: str | None = None
    model_name: str | None = None
    reference: str | None = None
    load_value: float | None = None
    load_unit: str | None = None
    speed_ratio: float | None = None
    acquired_at: str | None = None
    extra_fields: dict[str, ConditionScalar] = Field(default_factory=dict)

    @field_validator(
        "line",
        "device_id",
        "model_name",
        "reference",
        "load_unit",
        "acquired_at",
        mode="before",
    )
    @classmethod
    def empty_string_is_null(cls, value: Any) -> Any:
        if isinstance(value, str) and not value.strip():
            return None
        return value

    @field_validator("extra_fields", mode="before")
    @classmethod
    def validate_extra_fields(cls, value: Any) -> Any:
        if value is None:
            return {}
        if not isinstance(value, dict):
            raise ValueError("extra_fields must be an object")
        if len(value) > MAX_EXTRA_CONDITION_FIELDS:
            raise ValueError(
                f"extra_fields supports at most {MAX_EXTRA_CONDITION_FIELDS} fields"
            )
        normalized: dict[str, ConditionScalar] = {}
        for raw_key, field_value in value.items():
            if not isinstance(raw_key, str):
                raise ValueError("extra field names must be strings")
            key = raw_key.strip()
            if not key:
                raise ValueError("extra field names must not be empty")
            if len(key) > MAX_EXTRA_CONDITION_KEY_LENGTH:
                raise ValueError(
                    "extra field names must not exceed "
                    f"{MAX_EXTRA_CONDITION_KEY_LENGTH} characters"
                )
            if key in RESERVED_CONDITION_FIELD_NAMES:
                raise ValueError(f"extra field name is reserved: {key}")
            if key in normalized:
                raise ValueError(f"duplicate extra field name after trimming: {key}")
            if field_value is not None and not isinstance(
                field_value, (str, bool, int, float)
            ):
                raise ValueError(
                    f"extra field values must be scalar or null: {key}"
                )
            if isinstance(field_value, float) and not math.isfinite(field_value):
                raise ValueError(f"extra field numeric values must be finite: {key}")
            normalized[key] = field_value
        return normalized


class DatabaseCreateRequest(ApiModel):
    name: str = Field(min_length=1, max_length=120)


class DatabaseCreateFromFolderRequest(DatabaseCreateRequest):
    path: str = Field(min_length=1)


class DatabaseOpenRequest(ApiModel):
    path: str = Field(min_length=1)


class DatabaseUpdateRequest(ApiModel):
    name: str = Field(min_length=1, max_length=120)


class DatabaseLineCreateRequest(ApiModel):
    name: str = Field(min_length=1, max_length=120)


class TdmsLabelResolveRequest(ApiModel):
    path: str = Field(min_length=1)
    line: str = Field(default="", max_length=120)
    wav_profile: Literal["motor", "rail"] | None = None
    session_path: str | None = None
    taxonomy_path: str | None = None


class LabelSessionCreateRequest(ApiModel):
    paths: list[str] = Field(min_length=1)
    output_directory: str | None = None
    history_path: str | None = None
    source: str = Field(min_length=1, max_length=120, pattern=r"^(?:expert|operator)(?:_.+)?$")


class SignalAnalysisRequest(ApiModel):
    data: list[float] = Field(min_length=1)
    sampling_rate_hz: float = Field(gt=0)
    sample_id: str = Field(default="", max_length=80)
    card_id: Literal["dwt", "pcen", "mfcc", "mel_detail", "wavelet", "emd"]
    params: dict[str, Any] = Field(default_factory=dict)


class LabelEventCreateRequest(ApiModel):
    path: str = Field(min_length=1)
    line: str = Field(default="", max_length=120)
    wav_profile: Literal["motor", "rail"] | None = None
    session_path: str | None = None
    taxonomy_path: str | None = None
    source_sample_id: str = Field(min_length=1, max_length=80)
    sample_id: str = Field(min_length=1, max_length=80)
    target_event_uuid: str | None = None
    sample_scope: dict[str, float]
    scope_kind: Literal["event", "whole"] = "event"
    source: str = Field(min_length=1, max_length=120, pattern=r"^(?:expert|operator)(?:_.+)?$")
    result_key: str = Field(min_length=1, max_length=80)
    result_confidence: float | None = Field(default=None, ge=0, le=1)
    reason_key: str = Field(min_length=1, max_length=80)
    reason_confidence: float | None = Field(default=None, ge=0, le=1)
    note: str = Field(default="", max_length=2000)
    prototype: bool = False


class LabelProjectSaveRequest(ApiModel):
    project_id: str | None = None
    name: str = Field(min_length=1, max_length=120)
    root_path: str = Field(min_length=1)
    file_format: Literal["wav", "tdms"]
    file_type: Literal["rail", "motor", "generic"]
    taxonomy_path: str | None = None
    history_path: str | None = None


class LabelEventDeleteRequest(ApiModel):
    path: str = Field(min_length=1)
    session_path: str = Field(min_length=1)
    sample_id: str = Field(min_length=1)
    event_uuid: str = Field(min_length=1)


class LabelTaxonomyUpdateRequest(ApiModel):
    path: str | None = None
    results: list[dict[str, Any]]
    reasons: list[dict[str, Any]]


class StorageFolderSelectRequest(ApiModel):
    # Omit the path to open the native macOS folder chooser.  Supplying a path
    # keeps the endpoint deterministic for tests and managed launchers.
    path: str | None = None

    @field_validator("path", mode="before")
    @classmethod
    def empty_path_uses_native_picker(cls, value: Any) -> Any:
        if isinstance(value, str) and not value.strip():
            return None
        return value


class DatabaseFolderSelectRequest(StorageFolderSelectRequest):
    """Select a database catalog; independent from raw-data storage."""


class ChannelMapping(ApiModel):
    sample_id: str = Field(min_length=1, max_length=80)
    display_name: str | None = None
    group_name: str = Field(min_length=1)
    channel_name: str = Field(min_length=1)


class ImportRequest(ApiModel):
    preview_id: str | None = None
    source_paths: list[str] = Field(min_length=1)
    target_storage_id: str = "wuxi_raw"
    target_relative_dir: str = ""
    source_scope: Literal["inside", "outside"] | None = None
    transfer_mode: Literal["copy", "move"] = "copy"
    source_file_type: Literal["tdms", "wav"]
    conditions: Conditions = Field(default_factory=Conditions)
    sample_profile: str = "default"
    channel_mappings: list[ChannelMapping] | None = None


class ConditionUpdateRequest(ApiModel):
    file_uids: list[str] | None = Field(default=None, min_length=1)
    scope: Literal["database", "folder", "file"] | None = None
    relative_path: str | None = None
    conditions: Conditions

    @model_validator(mode="after")
    def validate_update_target(self) -> "ConditionUpdateRequest":
        has_file_uids = self.file_uids is not None
        has_scope = self.scope is not None
        if has_file_uids == has_scope:
            raise ValueError("provide either file_uids or scope, but not both")
        if has_file_uids:
            if self.relative_path is not None:
                raise ValueError("relative_path is only valid with scope")
            return self
        if self.scope in {"folder", "file"} and not self.relative_path:
            raise ValueError(f"relative_path is required for {self.scope} scope")
        if self.scope == "database" and self.relative_path is not None:
            raise ValueError("relative_path must be omitted for database scope")
        return self


class ReconcileApplyRequest(ApiModel):
    preview_id: str = Field(min_length=1)
    database_uid: str | None = Field(default=None, min_length=1, max_length=128)
    actions: list[dict[str, Any]] = Field(default_factory=list)
    replacement_conditions: Conditions | None = None
