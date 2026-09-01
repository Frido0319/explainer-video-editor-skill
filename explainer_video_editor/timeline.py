"""Auditable edit timeline loading and validation."""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


MAX_ZOOM = 1.08
SUPPORTED_EDIT_OPS = {"keep", "cut", "compress", "reorder", "zoom", "callout", "subtitle_rebase"}
DESTRUCTIVE_OPS = {"cut", "compress", "reorder"}
SAFE_IDENTIFIER = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]{0,63}\Z")


@dataclass(frozen=True)
class Source:
    id: str
    duration: float
    path: str | None = None
    generator: str | None = None


@dataclass(frozen=True)
class Segment:
    source: str
    source_start: float
    source_end: float
    output_start: float
    output_end: float
    speed: float = 1.0

    @property
    def source_duration(self) -> float:
        return self.source_end - self.source_start

    @property
    def output_duration(self) -> float:
        return self.output_end - self.output_start


@dataclass(frozen=True)
class VisualEvent:
    type: str
    start: float
    end: float
    payload: dict[str, Any]


@dataclass(frozen=True)
class SubtitleEvent:
    start: float
    end: float
    text: str


@dataclass(frozen=True)
class Timeline:
    data: dict[str, Any]
    path: Path | None
    project_dir: Path
    sources: dict[str, Source]
    operations: list[dict[str, Any]]
    width: int
    height: int
    fps: int
    output_dir: Path
    output_name: str


@dataclass(frozen=True)
class CompiledTimeline:
    source_timeline: Timeline
    segments: list[Segment]
    duration: float
    zooms: list[VisualEvent] = field(default_factory=list)
    callouts: list[VisualEvent] = field(default_factory=list)
    subtitles: list[SubtitleEvent] = field(default_factory=list)


def _resolve(path_value: str, project_dir: Path) -> str:
    expanded = os.path.expandvars(path_value)
    if "$" in expanded:
        raise ValueError(f"unresolved environment variable in path: {path_value}")
    path = Path(expanded)
    return str(path if path.is_absolute() else (project_dir / path).resolve())


def _require(mapping: dict[str, Any], keys: tuple[str, ...], context: str) -> None:
    missing = [key for key in keys if key not in mapping]
    if missing:
        raise ValueError(f"{context} missing fields: {', '.join(missing)}")


def _number(value: Any, label: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{label} must be numeric")
    return float(value)


def _safe_identifier(value: Any, context: str) -> str:
    if not isinstance(value, str) or SAFE_IDENTIFIER.fullmatch(value) is None:
        raise ValueError(f"{context} must be a safe identifier")
    return value


def subtitle_safe_y(height: int) -> int:
    return 820 if height >= 1080 else int(height * 0.76)


def _operation_range(operation: dict[str, Any], source_duration: float, context: str) -> tuple[float, float]:
    _require(operation, ("start", "end"), context)
    start = _number(operation["start"], f"{context}.start")
    end = _number(operation["end"], f"{context}.end")
    if not 0 <= start < end <= source_duration:
        raise ValueError(f"{context} outside source duration")
    return start, end


def _validate_authorization(operation: dict[str, Any]) -> None:
    if operation["type"] in DESTRUCTIVE_OPS and operation.get("authorized") is not True:
        raise PermissionError(f"explicit authorization required for destructive edit: {operation['type']}")


def _validate_source_operation_overlaps(operations: list[dict[str, Any]], sources: dict[str, Source]) -> None:
    ranges_by_source: dict[str, list[tuple[float, float, str]]] = {source_id: [] for source_id in sources}
    for operation in operations:
        if operation["type"] not in {"cut", "compress"}:
            continue
        source = sources[operation["source"]]
        start, end = _operation_range(operation, source.duration, operation["type"])
        ranges_by_source[source.id].append((start, end, operation["type"]))

    for source_id, ranges in ranges_by_source.items():
        previous_end = -1.0
        previous_kind = ""
        for start, end, kind in sorted(ranges):
            if start < previous_end - 1e-6:
                raise ValueError(f"source operations overlap for {source_id}: {previous_kind} and {kind}")
            previous_end = end
            previous_kind = kind


def load_timeline_from_data(
    data: dict[str, Any], project_dir: Path, path: Path | None = None
) -> Timeline:
    _require(
        data,
        ("version", "mode", "fps", "width", "height", "output_dir", "output_name", "sources", "operations"),
        "edit timeline",
    )
    if data["version"] != 2:
        raise ValueError("edit timeline version must be 2")
    if data["mode"] != "edit":
        raise ValueError(f"unsupported edit timeline mode: {data['mode']}")
    width = int(data["width"])
    height = int(data["height"])
    fps = int(data["fps"])
    if width <= 0 or height <= 0 or fps <= 0:
        raise ValueError("edit width, height, and fps must be positive")

    output_name = data["output_name"]
    if (
        not isinstance(output_name, str)
        or not output_name.strip()
        or output_name in {".", ".."}
        or Path(output_name).is_absolute()
        or Path(output_name).name != output_name
        or "\\" in output_name
    ):
        raise ValueError("output_name must be a plain filename inside output_dir")
    data["output_dir"] = _resolve(str(data["output_dir"]), project_dir)

    if not isinstance(data["sources"], list) or not data["sources"]:
        raise ValueError("edit timeline requires at least one source")
    sources: dict[str, Source] = {}
    for index, source in enumerate(data["sources"]):
        if not isinstance(source, dict):
            raise ValueError(f"source {index} must be an object")
        _require(source, ("id", "duration"), f"source {index}")
        source_id = _safe_identifier(source["id"], f"source {index} id")
        if source_id in sources:
            raise ValueError(f"duplicate source id: {source_id}")
        duration = _number(source["duration"], f"source {source_id}.duration")
        if duration <= 0:
            raise ValueError(f"source {source_id} duration must be positive")
        path_value = source.get("path")
        generator = source.get("generator")
        if path_value:
            source["path"] = _resolve(str(path_value), project_dir)
        elif generator != "synthetic":
            raise ValueError(f"source {source_id} requires a path or synthetic generator")
        sources[source_id] = Source(source_id, duration, source.get("path"), generator)

    if not isinstance(data["operations"], list):
        raise ValueError("operations must be a list")
    for index, operation in enumerate(data["operations"]):
        if not isinstance(operation, dict):
            raise ValueError(f"operation {index} must be an object")
        operation_type = operation.get("type")
        if operation_type not in SUPPORTED_EDIT_OPS:
            raise ValueError(f"unsupported edit operation: {operation_type}")
        _validate_authorization(operation)
        if operation_type in {"keep", "cut", "compress", "zoom", "subtitle_rebase"}:
            source_id = operation.get("source")
            if source_id not in sources:
                raise ValueError(f"unknown source reference: {source_id}")
            source = sources[source_id]
            if operation_type != "subtitle_rebase":
                _operation_range(operation, source.duration, operation_type)
        if operation_type == "compress":
            factor = _number(operation.get("factor"), "compress.factor")
            if factor <= 1.0:
                raise ValueError("compress.factor must be greater than 1.0")
        elif operation_type == "zoom":
            zoom = _number(operation.get("zoom"), "zoom.zoom")
            if zoom < 1.0 or zoom > MAX_ZOOM:
                raise ValueError(f"zoom must stay between 1.0 and {MAX_ZOOM:.2f}")
        elif operation_type == "callout":
            _require(operation, ("start", "end", "text", "x", "y", "width", "height"), "callout")
            start = _number(operation["start"], "callout.start")
            end = _number(operation["end"], "callout.end")
            x = _number(operation["x"], "callout.x")
            y = _number(operation["y"], "callout.y")
            callout_width = _number(operation["width"], "callout.width")
            callout_height = _number(operation["height"], "callout.height")
            if not 0 <= start < end:
                raise ValueError("callout must have positive timing")
            if x < 0 or y < 0 or callout_width <= 0 or callout_height <= 0 or x + callout_width > width:
                raise ValueError("callout must stay inside the video frame")
            if y + callout_height > subtitle_safe_y(height):
                raise ValueError("callout violates subtitle safe area")
            if not str(operation["text"]).strip():
                raise ValueError("callout text must not be empty")
        elif operation_type == "reorder":
            source_id = operation.get("source")
            if source_id not in sources:
                raise ValueError(f"unknown source reference: {source_id}")
            ranges = operation.get("ranges")
            if not isinstance(ranges, list) or not ranges:
                raise ValueError("reorder requires ranges")
            for range_index, item in enumerate(ranges):
                if not isinstance(item, dict):
                    raise ValueError("reorder ranges must be objects")
                _operation_range(item, sources[source_id].duration, f"reorder range {range_index}")
        elif operation_type == "subtitle_rebase":
            _require(operation, ("path",), "subtitle_rebase")
            operation["path"] = _resolve(str(operation["path"]), project_dir)

    _validate_source_operation_overlaps(data["operations"], sources)
    return Timeline(
        data=data,
        path=path,
        project_dir=project_dir,
        sources=sources,
        operations=data["operations"],
        width=width,
        height=height,
        fps=fps,
        output_dir=Path(data["output_dir"]),
        output_name=output_name,
    )


def load_timeline(path: Path) -> Timeline:
    timeline_path = Path(path).resolve()
    data = json.loads(timeline_path.read_text(encoding="utf-8"))
    return load_timeline_from_data(data, timeline_path.parent, timeline_path)
