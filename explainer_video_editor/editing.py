"""Public edit-mode runtime for existing videos."""

from __future__ import annotations

import json
import re
import shlex
import subprocess
from pathlib import Path
from typing import Any

from .timeline import (
    CompiledTimeline,
    Segment,
    SubtitleEvent,
    Timeline,
    VisualEvent,
    load_timeline_from_data,
)


def _source_ops(timeline: Timeline, operation_type: str) -> list[dict[str, Any]]:
    return [operation for operation in timeline.operations if operation["type"] == operation_type]


def _append_segment(
    segments: list[Segment],
    source: str,
    source_start: float,
    source_end: float,
    output_start: float,
    speed: float,
) -> float:
    if source_end <= source_start:
        return output_start
    output_end = output_start + (source_end - source_start) / speed
    segments.append(Segment(source, source_start, source_end, output_start, output_end, speed))
    return output_end


def _merge_ranges(ranges: list[tuple[float, float]]) -> list[tuple[float, float]]:
    merged: list[tuple[float, float]] = []
    for start, end in sorted(ranges):
        if not merged or start > merged[-1][1] + 1e-6:
            merged.append((start, end))
        else:
            merged[-1] = (merged[-1][0], max(merged[-1][1], end))
    return merged


def _plain_segment(source_id: str, start: float, end: float, speed: float) -> Segment | None:
    if end <= start + 1e-6:
        return None
    return Segment(source_id, start, end, 0.0, 0.0, speed)


def _clip_segment(segment: Segment, start: float, end: float) -> Segment | None:
    return _plain_segment(
        segment.source,
        max(segment.source_start, start),
        min(segment.source_end, end),
        segment.speed,
    )


def _subtract_ranges(segment: Segment, covered: list[tuple[float, float]]) -> list[Segment]:
    remaining: list[Segment] = []
    cursor = segment.source_start
    for start, end in covered:
        if end <= cursor + 1e-6 or start >= segment.source_end - 1e-6:
            continue
        if start > cursor + 1e-6:
            piece = _plain_segment(segment.source, cursor, min(start, segment.source_end), segment.speed)
            if piece is not None:
                remaining.append(piece)
        cursor = max(cursor, end)
        if cursor >= segment.source_end - 1e-6:
            break
    if cursor < segment.source_end - 1e-6:
        piece = _plain_segment(segment.source, cursor, segment.source_end, segment.speed)
        if piece is not None:
            remaining.append(piece)
    return remaining


def _materialize_output(segments: list[Segment]) -> list[Segment]:
    output = 0.0
    materialized: list[Segment] = []
    for segment in segments:
        output = _append_segment(
            materialized,
            segment.source,
            segment.source_start,
            segment.source_end,
            output,
            segment.speed,
        )
    return materialized


def _retained_ranges(timeline: Timeline, source_id: str) -> list[tuple[float, float]]:
    keep_ranges = [
        (float(operation["start"]), float(operation["end"]))
        for operation in _source_ops(timeline, "keep")
        if operation["source"] == source_id
    ]
    if keep_ranges:
        return _merge_ranges(keep_ranges)
    return [(0.0, timeline.sources[source_id].duration)]


def _compile_source_segments(timeline: Timeline, source_id: str) -> list[Segment]:
    transforms = sorted(
        [
            operation
            for operation in timeline.operations
            if operation["type"] in {"cut", "compress"} and operation["source"] == source_id
        ],
        key=lambda operation: (float(operation["start"]), float(operation["end"])),
    )
    segments: list[Segment] = []
    for retain_start, retain_end in _retained_ranges(timeline, source_id):
        cursor = retain_start
        for operation in transforms:
            start = max(float(operation["start"]), retain_start)
            end = min(float(operation["end"]), retain_end)
            if end <= start + 1e-6:
                continue
            piece = _plain_segment(source_id, cursor, start, 1.0)
            if piece is not None:
                segments.append(piece)
            if operation["type"] == "compress":
                piece = _plain_segment(source_id, start, end, float(operation["factor"]))
                if piece is not None:
                    segments.append(piece)
            cursor = max(cursor, end)
        piece = _plain_segment(source_id, cursor, retain_end, 1.0)
        if piece is not None:
            segments.append(piece)
    return segments


def _compile_segments(timeline: Timeline) -> list[Segment]:
    base_segments = {
        source_id: _compile_source_segments(timeline, source_id) for source_id in timeline.sources
    }

    reorder_ops = _source_ops(timeline, "reorder")
    if not reorder_ops:
        ordered = [segment for source_id in timeline.sources for segment in base_segments[source_id]]
        return _materialize_output(ordered)

    explicit: list[Segment] = []
    consumed_ranges: dict[str, list[tuple[float, float]]] = {source_id: [] for source_id in timeline.sources}
    for operation in reorder_ops:
        source_id = operation["source"]
        for item in operation["ranges"]:
            start = float(item["start"])
            end = float(item["end"])
            for segment in base_segments[source_id]:
                piece = _clip_segment(segment, start, end)
                if piece is not None:
                    explicit.append(piece)
                    consumed_ranges[source_id].append((piece.source_start, piece.source_end))

    ordered = list(explicit)
    for source_id in timeline.sources:
        covered = _merge_ranges(consumed_ranges[source_id])
        for segment in base_segments[source_id]:
            ordered.extend(_subtract_ranges(segment, covered))
    return _materialize_output(ordered)


def _map_time(segments: list[Segment], source_id: str, source_time: float) -> float | None:
    for segment in segments:
        if segment.source != source_id:
            continue
        if segment.source_start - 1e-6 <= source_time <= segment.source_end + 1e-6:
            return segment.output_start + (source_time - segment.source_start) / segment.speed
    return None


def _compile_visuals(timeline: Timeline, segments: list[Segment]) -> tuple[list[VisualEvent], list[VisualEvent]]:
    zooms: list[VisualEvent] = []
    callouts: list[VisualEvent] = []
    for operation in timeline.operations:
        if operation["type"] == "zoom":
            start = _map_time(segments, operation["source"], float(operation["start"]))
            end = _map_time(segments, operation["source"], float(operation["end"]))
            if start is not None and end is not None and end > start:
                zooms.append(VisualEvent("zoom", start, end, dict(operation)))
        elif operation["type"] == "callout":
            callouts.append(VisualEvent("callout", float(operation["start"]), float(operation["end"]), dict(operation)))
    return zooms, callouts


def _parse_ass_time(value: str) -> float:
    match = re.fullmatch(r"(\d+):(\d{2}):(\d{2})\.(\d{2})", value.strip())
    if not match:
        raise ValueError(f"invalid ASS timestamp: {value}")
    hours, minutes, seconds, centiseconds = map(int, match.groups())
    return hours * 3600 + minutes * 60 + seconds + centiseconds / 100


def _format_ass_time(seconds: float) -> str:
    total_centiseconds = max(0, int(round(seconds * 100)))
    hours, remainder = divmod(total_centiseconds, 360000)
    minutes, remainder = divmod(remainder, 6000)
    whole_seconds, centiseconds = divmod(remainder, 100)
    return f"{hours}:{minutes:02d}:{whole_seconds:02d}.{centiseconds:02d}"


def _parse_ass_events(path: Path) -> list[SubtitleEvent]:
    events: list[SubtitleEvent] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.startswith("Dialogue:"):
            continue
        fields = line.split(",", 9)
        if len(fields) < 10:
            raise ValueError(f"malformed ASS dialogue: {line}")
        events.append(SubtitleEvent(_parse_ass_time(fields[1]), _parse_ass_time(fields[2]), fields[9]))
    return events


def _compile_subtitles(timeline: Timeline, segments: list[Segment]) -> list[SubtitleEvent]:
    rebased: list[SubtitleEvent] = []
    for operation in _source_ops(timeline, "subtitle_rebase"):
        source_id = operation["source"]
        for event in _parse_ass_events(Path(operation["path"])):
            start = _map_time(segments, source_id, event.start)
            end = _map_time(segments, source_id, event.end)
            if start is None or end is None or end <= start:
                continue
            rebased.append(SubtitleEvent(start, end, event.text))
    return sorted(rebased, key=lambda event: (event.start, event.end, event.text))


def compile_edit_timeline(timeline: Timeline) -> CompiledTimeline:
    segments = _compile_segments(timeline)
    duration = segments[-1].output_end if segments else 0.0
    zooms, callouts = _compile_visuals(timeline, segments)
    subtitles = _compile_subtitles(timeline, segments)
    return CompiledTimeline(timeline, segments, duration, zooms, callouts, subtitles)


def _run(command: list[str]) -> None:
    print("运行：", " ".join(shlex.quote(item) for item in command))
    subprocess.run(command, check=True)


def _atempo_chain(speed: float) -> str:
    factors: list[float] = []
    remaining = speed
    while remaining > 2.0:
        factors.append(2.0)
        remaining /= 2.0
    while remaining < 0.5:
        factors.append(0.5)
        remaining /= 0.5
    factors.append(remaining)
    return ",".join(f"atempo={factor:.6g}" for factor in factors)


def _ensure_source_media(timeline: Timeline, source_id: str, source_paths: dict[str, Path], work_dir: Path) -> Path:
    if source_id in source_paths:
        return source_paths[source_id]
    source = timeline.sources[source_id]
    if source.path:
        path = Path(source.path)
    else:
        path = work_dir / f"{source.id}_synthetic_source.mp4"
        _run(
            [
                "ffmpeg",
                "-y",
                "-hide_banner",
                "-loglevel",
                "warning",
                "-f",
                "lavfi",
                "-i",
                f"testsrc2=size={timeline.width}x{timeline.height}:rate={timeline.fps}:duration={source.duration:.3f}",
                "-f",
                "lavfi",
                "-i",
                f"sine=frequency=440:sample_rate=44100:duration={source.duration:.3f}",
                "-map",
                "0:v:0",
                "-map",
                "1:a:0",
                "-c:v",
                "libx264",
                "-preset",
                "ultrafast",
                "-crf",
                "23",
                "-pix_fmt",
                "yuv420p",
                "-c:a",
                "aac",
                "-b:a",
                "128k",
                "-ar",
                "44100",
                "-ac",
                "2",
                "-movflags",
                "+faststart",
                str(path),
            ]
        )
    if not path.is_file():
        raise FileNotFoundError(path)
    source_paths[source_id] = path
    return path


def _render_segment(timeline: Timeline, segment: Segment, source_path: Path, destination: Path) -> None:
    duration = segment.source_duration
    video_filter = (
        f"[0:v]setpts=(PTS-STARTPTS)/{segment.speed:.6g},"
        f"scale={timeline.width}:{timeline.height}:flags=lanczos,fps={timeline.fps},setsar=1[v]"
    )
    audio_filter = "[0:a]asetpts=PTS-STARTPTS[a]" if abs(segment.speed - 1.0) < 1e-6 else f"[0:a]{_atempo_chain(segment.speed)},asetpts=PTS-STARTPTS[a]"
    _run(
        [
            "ffmpeg",
            "-y",
            "-hide_banner",
            "-loglevel",
            "warning",
            "-ss",
            f"{segment.source_start:.3f}",
            "-t",
            f"{duration:.3f}",
            "-i",
            str(source_path),
            "-filter_complex",
            f"{video_filter};{audio_filter}",
            "-map",
            "[v]",
            "-map",
            "[a]",
            "-c:v",
            "libx264",
            "-preset",
            "ultrafast",
            "-crf",
            "20",
            "-pix_fmt",
            "yuv420p",
            "-c:a",
            "aac",
            "-b:a",
            "160k",
            "-ar",
            "44100",
            "-ac",
            "2",
            str(destination),
        ]
    )


def _concat_segments(segment_paths: list[Path], destination: Path, work_dir: Path) -> None:
    list_path = work_dir / "concat.txt"
    lines = []
    for path in segment_paths:
        escaped = str(path).replace("'", "'\\''")
        lines.append(f"file '{escaped}'\n")
    list_path.write_text("".join(lines), encoding="utf-8")
    _run(
        [
            "ffmpeg",
            "-y",
            "-hide_banner",
            "-loglevel",
            "warning",
            "-f",
            "concat",
            "-safe",
            "0",
            "-i",
            str(list_path),
            "-c",
            "copy",
            str(destination),
        ]
    )


def _write_ass(events: list[SubtitleEvent], destination: Path, width: int, height: int) -> Path | None:
    lines = [
        "[Script Info]",
        "ScriptType: v4.00+",
        f"PlayResX: {width}",
        f"PlayResY: {height}",
        "[V4+ Styles]",
        "Format: Name, Fontname, Fontsize, PrimaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding",
        "Style: Default,DejaVu Sans,20,&H00FFFFFF,&H00000000,&H64000000,0,0,0,0,100,100,0,0,1,2,0,2,24,24,24,1",
        "[Events]",
        "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text",
    ]
    for event in events:
        text = event.text.replace("\n", "\\N")
        lines.append(f"Dialogue: 0,{_format_ass_time(event.start)},{_format_ass_time(event.end)},Default,,0,0,0,,{text}")
    destination.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return destination if events else None


def _filter_path(path: Path) -> str:
    return str(path).replace("\\", "\\\\").replace(":", "\\:").replace("'", "\\'")


def _escape_drawtext(text: str) -> str:
    return text.replace("\\", "\\\\").replace(":", "\\:").replace("'", "\\'")


def _final_video_filter(timeline: Timeline, compiled: CompiledTimeline, ass_path: Path | None) -> str:
    chain = "null"
    if compiled.zooms:
        expressions = [
            f"between(on/{timeline.fps}\\,{event.start:.3f}\\,{event.end:.3f})*{float(event.payload['zoom']):.5f}"
            for event in compiled.zooms
        ]
        zoom_expr = "+".join(expressions)
        chain = (
            f"scale={timeline.width * 4}:{timeline.height * 4}:flags=lanczos,"
            f"zoompan=z='max(1.0\\,{zoom_expr})':x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)':"
            f"d=1:s={timeline.width}x{timeline.height}:fps={timeline.fps},setsar=1"
        )
    for event in compiled.callouts:
        payload = event.payload
        enable = f"between(t\\,{event.start:.3f}\\,{event.end:.3f})"
        chain += (
            f",drawbox=x={int(payload['x'])}:y={int(payload['y'])}:w={int(payload['width'])}:h={int(payload['height'])}:"
            f"color=0x0d3660@0.72:t=fill:enable='{enable}'"
            f",drawtext=text='{_escape_drawtext(str(payload['text']))}':x={int(payload['x']) + 8}:y={int(payload['y']) + 8}:"
            f"fontsize=18:fontcolor=white:enable='{enable}'"
        )
    if ass_path is not None:
        chain += f",subtitles='{_filter_path(ass_path)}'"
    return f"[0:v]{chain}[vout]"


def build_edit(project: dict[str, Any]) -> Path:
    timeline = load_timeline_from_data(project, Path(project["_manifest_path"]).parent)
    compiled = compile_edit_timeline(timeline)
    output_dir = timeline.output_dir
    work_dir = output_dir / "work"
    output_dir.mkdir(parents=True, exist_ok=True)
    work_dir.mkdir(parents=True, exist_ok=True)

    source_paths: dict[str, Path] = {}
    segment_paths: list[Path] = []
    for index, segment in enumerate(compiled.segments):
        source_path = _ensure_source_media(timeline, segment.source, source_paths, work_dir)
        segment_path = work_dir / f"segment_{index:03d}.mp4"
        _render_segment(timeline, segment, source_path, segment_path)
        segment_paths.append(segment_path)
    base = work_dir / "edit_base.mp4"
    _concat_segments(segment_paths, base, work_dir)

    ass_path = _write_ass(compiled.subtitles, work_dir / "rebased.ass", timeline.width, timeline.height)
    output = output_dir / timeline.output_name
    _run(
        [
            "ffmpeg",
            "-y",
            "-hide_banner",
            "-loglevel",
            "warning",
            "-i",
            str(base),
            "-filter_complex",
            _final_video_filter(timeline, compiled, ass_path),
            "-map",
            "[vout]",
            "-map",
            "0:a:0",
            "-t",
            f"{compiled.duration:.3f}",
            "-c:v",
            "libx264",
            "-preset",
            "medium",
            "-crf",
            "18",
            "-pix_fmt",
            "yuv420p",
            "-c:a",
            "aac",
            "-b:a",
            "192k",
            "-ar",
            "44100",
            "-ac",
            "2",
            "-movflags",
            "+faststart",
            str(output),
        ]
    )
    _write_edit_report(project, compiled, output, work_dir)
    return output


def _probe(path: Path) -> dict[str, Any]:
    result = subprocess.run(
        [
            "ffprobe",
            "-v",
            "error",
            "-show_entries",
            "format=duration,size:stream=codec_name,codec_type,width,height,r_frame_rate,sample_rate,channels",
            "-of",
            "json",
            str(path),
        ],
        text=True,
        capture_output=True,
        check=True,
    )
    return json.loads(result.stdout)


def _write_edit_report(project: dict[str, Any], compiled: CompiledTimeline, output: Path, work_dir: Path) -> None:
    report = {
        "mode": "edit",
        "output": str(output),
        "duration": compiled.duration,
        "segments": [segment.__dict__ for segment in compiled.segments],
        "zooms": [event.__dict__ for event in compiled.zooms],
        "callouts": [event.__dict__ for event in compiled.callouts],
        "subtitle_events": [event.__dict__ for event in compiled.subtitles],
    }
    (work_dir / "edit_timeline.compiled.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
    )


def verify_edit_project(project: dict[str, Any]) -> dict[str, Any]:
    timeline = load_timeline_from_data(project, Path(project["_manifest_path"]).parent)
    compiled = compile_edit_timeline(timeline)
    output = timeline.output_dir / timeline.output_name
    if not output.is_file():
        raise FileNotFoundError(output)
    metadata = _probe(output)
    duration = float(metadata["format"]["duration"])
    tolerance = float(project.get("verification", {}).get("duration_tolerance", 0.25))
    if abs(duration - compiled.duration) > tolerance:
        raise RuntimeError(f"duration mismatch: expected {compiled.duration:.3f}, got {duration:.3f}")
    video = next((stream for stream in metadata.get("streams", []) if stream.get("codec_type") == "video"), None)
    audio = next((stream for stream in metadata.get("streams", []) if stream.get("codec_type") == "audio"), None)
    expected_video = ("h264", timeline.width, timeline.height)
    actual_video = None if video is None else (video.get("codec_name"), video.get("width"), video.get("height"))
    if actual_video != expected_video:
        raise RuntimeError(f"video specification mismatch: expected {expected_video}, got {actual_video}")
    expected_audio = ("aac", 44100, 2)
    actual_audio = None if audio is None else (
        audio.get("codec_name"),
        int(audio.get("sample_rate", 0)),
        audio.get("channels"),
    )
    if actual_audio != expected_audio:
        raise RuntimeError(f"audio specification mismatch: expected {expected_audio}, got {actual_audio}")
    return {
        "output": str(output),
        "duration": duration,
        "expected_duration": compiled.duration,
        "metadata": metadata,
        "compiled_timeline": str(timeline.output_dir / "work" / "edit_timeline.compiled.json"),
        "subtitles": str(timeline.output_dir / "work" / "rebased.ass"),
    }
