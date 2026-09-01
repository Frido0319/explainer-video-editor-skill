"""Load and validate public create/edit project manifests."""

from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import Any

from .pronunciation import rewrite_narration


Project = dict[str, Any]


SUPPORTED_MODES = {"create", "edit"}
SUPPORTED_THEMES = {"research_ppt"}
SUPPORTED_VISUAL_KINDS = {"card", "clip", "image", "pptx"}
SUPPORTED_CARD_TEMPLATES = {
    "hero",
    "process",
    "metric_compare",
    "chapter",
    "metric_grid",
    "ending",
}
SAFE_IDENTIFIER = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]{0,63}\Z")


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


def _validate_safe_identifier(value: Any, context: str) -> None:
    if not isinstance(value, str) or SAFE_IDENTIFIER.fullmatch(value) is None:
        raise ValueError(
            f"{context} must be a safe identifier using letters, digits, underscores, or hyphens"
        )


def _require_item_fields(
    values: Any,
    keys: tuple[str, ...],
    context: str,
    minimum: int,
    maximum: int,
) -> None:
    if not isinstance(values, list) or not minimum <= len(values) <= maximum:
        raise ValueError(f"{context} must contain {minimum} to {maximum} items")
    for index, value in enumerate(values):
        if not isinstance(value, dict):
            raise ValueError(f"{context} item {index} must be an object")
        _require(value, keys, f"{context} item {index}")


def _validate_takeaway(value: Any, context: str) -> None:
    if not isinstance(value, dict):
        raise ValueError(f"{context} must be an object")
    _require(value, ("lead", "detail"), context)


def _validate_card_spec(card: dict[str, Any], context: str) -> None:
    template = card["template"]
    required = {
        "hero": ("kicker", "title_lines"),
        "process": ("title", "steps", "takeaway"),
        "metric_compare": ("title", "before", "after", "takeaway"),
        "chapter": ("title", "items", "takeaway"),
        "metric_grid": ("title", "metrics", "takeaway"),
        "ending": ("brand", "headline", "subline", "badge"),
    }
    _require(card, required[template], f"{context} {template}")
    if template == "hero":
        _require_item_fields(card["title_lines"], ("text",), f"{context} title_lines", 1, 2)
        if "stats" in card:
            _require_item_fields(card["stats"], ("text",), f"{context} stats", 0, 2)
            for stat in card["stats"]:
                if stat.get("style", "light") not in {"blue", "light", "red"}:
                    raise ValueError(f"{context} stat style must be blue, light, or red")
    elif template == "process":
        _require_item_fields(card["steps"], ("number", "text"), f"{context} steps", 2, 4)
        _validate_takeaway(card["takeaway"], f"{context} takeaway")
    elif template == "metric_compare":
        _validate_takeaway(card["takeaway"], f"{context} takeaway")
    elif template == "chapter":
        _require_item_fields(card["items"], ("title", "detail"), f"{context} items", 2, 3)
        _validate_takeaway(card["takeaway"], f"{context} takeaway")
    elif template == "metric_grid":
        _require_item_fields(card["metrics"], ("value", "label"), f"{context} metrics", 2, 3)
        _validate_takeaway(card["takeaway"], f"{context} takeaway")


def validate_create_project(data: Project) -> None:
    _require(
        data,
        (
            "version",
            "mode",
            "theme",
            "duration",
            "fps",
            "width",
            "height",
            "output_dir",
            "output_name",
            "visuals",
            "narration",
            "verification",
        ),
        "manifest",
    )
    if data["version"] != 2:
        raise ValueError("manifest version must be 2")
    if data["mode"] != "create":
        raise ValueError(f"unsupported mode: {data['mode']}")
    if data["theme"] not in SUPPORTED_THEMES:
        raise ValueError(f"unsupported theme: {data['theme']}")

    duration = float(data["duration"])
    if duration <= 0 or int(data["fps"]) <= 0 or int(data["width"]) <= 0 or int(data["height"]) <= 0:
        raise ValueError("duration, fps, width, and height must be positive")
    if (int(data["width"]), int(data["height"]), int(data["fps"])) != (1920, 1080, 24):
        raise ValueError("public create output must be 1920x1080 at 24 fps")

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

    visuals = data["visuals"]
    if not visuals:
        raise ValueError("visual timeline must not be empty")
    if abs(float(visuals[0]["start"])) > 1e-6 or abs(float(visuals[-1]["end"]) - duration) > 1e-6:
        raise ValueError("visual timeline must cover the full duration")

    seen_ids: set[str] = set()
    for index, visual in enumerate(visuals):
        _require(visual, ("id", "kind", "start", "end"), f"visual {index}")
        _validate_safe_identifier(visual["id"], f"visual {index} id")
        if visual["id"] in seen_ids:
            raise ValueError(f"duplicate visual id: {visual['id']}")
        seen_ids.add(visual["id"])
        if visual["kind"] not in SUPPORTED_VISUAL_KINDS:
            raise ValueError(f"unsupported visual kind: {visual['kind']}")
        if float(visual["start"]) >= float(visual["end"]):
            raise ValueError(f"invalid visual segment: {visual['id']}")
        if index and abs(float(visuals[index - 1]["end"]) - float(visual["start"])) > 1e-6:
            raise ValueError("visual timeline must be contiguous")
        if visual["kind"] == "card":
            _require(visual, ("card",), f"card visual {visual['id']}")
            template = visual["card"].get("template")
            if template not in SUPPORTED_CARD_TEMPLATES:
                raise ValueError(f"unsupported card template: {template}")
            _validate_card_spec(visual["card"], f"card visual {visual['id']}")
        elif visual["kind"] == "clip":
            _require(
                visual,
                ("source", "source_start", "source_duration"),
                f"clip visual {visual['id']}",
            )
            if float(visual["source_start"]) < 0 or float(visual["source_duration"]) <= 0:
                raise ValueError(f"invalid clip source timing: {visual['id']}")
        elif visual["kind"] == "image":
            _require(visual, ("source",), f"image visual {visual['id']}")
        else:
            _require(visual, ("source", "slide"), f"pptx visual {visual['id']}")
            slide = visual["slide"]
            if not isinstance(slide, int) or isinstance(slide, bool) or slide <= 0:
                raise ValueError("slide must be a positive integer")

    narration_ids: set[str] = set()
    for narration in data["narration"]:
        _require(narration, ("id", "start", "end_limit", "text"), "narration")
        _validate_safe_identifier(narration["id"], "narration id")
        if narration["id"] in narration_ids:
            raise ValueError(f"duplicate narration id: {narration['id']}")
        narration_ids.add(narration["id"])
        if not 0 <= float(narration["start"]) < float(narration["end_limit"]) <= duration:
            raise ValueError(f"narration outside timeline: {narration['id']}")
        if not str(narration["text"]).strip():
            raise ValueError(f"empty narration: {narration['id']}")

    verification = data["verification"]
    _require(verification, ("frame_times", "duration_tolerance"), "verification")
    if not verification["frame_times"]:
        raise ValueError("verification requires at least one frame time")
    if any(not 0 <= float(value) <= duration for value in verification["frame_times"]):
        raise ValueError("verification frame time outside timeline")
    if float(verification["duration_tolerance"]) < 0:
        raise ValueError("duration tolerance must be non-negative")


def validate_project(data: Project) -> None:
    if data.get("mode") == "edit":
        from .timeline import load_timeline_from_data

        project_dir = Path(data.get("_manifest_path", ".")).resolve().parent
        load_timeline_from_data(data, project_dir)
        return
    validate_create_project(data)


def _assert_sources_exist(data: Project) -> None:
    if data["mode"] == "edit":
        for source in data["sources"]:
            if source.get("generator") == "synthetic":
                continue
            path = Path(source["path"])
            if not path.is_file():
                raise FileNotFoundError(path)
        for operation in data["operations"]:
            if operation.get("type") == "subtitle_rebase":
                path = Path(operation["path"])
                if not path.is_file():
                    raise FileNotFoundError(path)
        return
    for visual in data["visuals"]:
        if visual["kind"] in {"clip", "image", "pptx"}:
            source = Path(visual["source"])
            if not source.is_file():
                raise FileNotFoundError(source)
    bgm = data.get("audio", {}).get("bgm")
    if bgm and not Path(bgm).is_file():
        raise FileNotFoundError(bgm)


def _assert_output_does_not_overwrite_source(data: Project) -> None:
    output_dir = Path(data["output_dir"])
    output_root = output_dir.resolve(strict=False)
    output = (output_dir / data["output_name"]).resolve(strict=False)
    if data["mode"] == "edit":
        sources = [Path(source["path"]) for source in data["sources"] if source.get("path")]
        sources.extend(Path(operation["path"]) for operation in data["operations"] if operation.get("type") == "subtitle_rebase")
    else:
        sources = [
            Path(visual["source"])
            for visual in data["visuals"]
            if visual["kind"] in {"clip", "image", "pptx"}
        ]
    bgm = data.get("audio", {}).get("bgm")
    if bgm:
        sources.append(Path(bgm))
    for source in sources:
        resolved_source = source.resolve(strict=False)
        if resolved_source == output:
            raise ValueError(f"output path would overwrite source: {source}")
        if resolved_source == output_root or output_root in resolved_source.parents:
            raise ValueError(f"source must be outside output_dir: {source}")
    if output_dir.is_symlink():
        raise ValueError(f"output_dir must not be a symbolic link: {output_dir}")
    if output_dir.exists():
        symlink = next((path for path in output_dir.rglob("*") if path.is_symlink()), None)
        if symlink is not None:
            raise ValueError(f"output_dir contains a symbolic link: {symlink}")


def load_project(path: Path) -> Project:
    manifest_path = Path(path).resolve()
    raw = json.loads(manifest_path.read_text(encoding="utf-8"))
    data = rewrite_narration(raw)
    project_dir = manifest_path.parent
    data["_manifest_path"] = str(manifest_path)
    if data.get("mode") == "edit":
        from .editing import compile_edit_timeline
        from .timeline import load_timeline_from_data

        timeline = load_timeline_from_data(data, project_dir, manifest_path)
        data["duration"] = compile_edit_timeline(timeline).duration
        _assert_sources_exist(data)
        _assert_output_does_not_overwrite_source(data)
        return data
    data["output_dir"] = _resolve(str(data["output_dir"]), project_dir)
    for visual in data["visuals"]:
        if visual["kind"] in {"clip", "image", "pptx"}:
            visual["source"] = _resolve(str(visual["source"]), project_dir)
    audio = data.setdefault("audio", {})
    if audio.get("bgm"):
        audio["bgm"] = _resolve(str(audio["bgm"]), project_dir)
    else:
        audio["bgm"] = None
    validate_create_project(data)
    _assert_sources_exist(data)
    _assert_output_does_not_overwrite_source(data)
    return data
