"""Project preparation and end-to-end public create build orchestration."""

from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Any

from .audio import build_audio_assets
from .manifest import Project
from .media import build_visuals
from .provenance import write_build_fingerprint
from .themes import get_theme


def final_ffmpeg_command(
    video_only: Path,
    audio: Path,
    subtitles: Path,
    output: Path,
    duration: float,
) -> list[str]:
    sub_path = str(subtitles).replace(":", "\\:").replace("'", "\\'")
    return [
        "ffmpeg",
        "-y",
        "-hide_banner",
        "-loglevel",
        "warning",
        "-i",
        str(video_only),
        "-i",
        str(audio),
        "-vf",
        f"subtitles='{sub_path}'",
        "-map",
        "0:v:0",
        "-map",
        "1:a:0",
        "-t",
        f"{duration:.3f}",
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


def build(project: Project) -> Path:
    if project["mode"] == "edit":
        from .editing import build_edit

        return build_edit(project)
    data: dict[str, Any] = project
    output_dir = Path(data["output_dir"])
    work_dir = output_dir / "work"
    output_dir.mkdir(parents=True, exist_ok=True)
    work_dir.mkdir(parents=True, exist_ok=True)
    theme = get_theme(data["theme"])
    video_only = build_visuals(data, theme, work_dir)
    bgm_value = data.get("audio", {}).get("bgm")
    bgm_path = Path(bgm_value) if bgm_value else None
    full_audio, subtitles, _ = build_audio_assets(data, work_dir, bgm_path)
    output = output_dir / data["output_name"]
    command = final_ffmpeg_command(video_only, full_audio, subtitles, output, float(data["duration"]))
    print("运行：", " ".join(command))
    subprocess.run(command, check=True)
    write_build_fingerprint(data, work_dir, output)
    return output
