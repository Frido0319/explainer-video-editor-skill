from __future__ import annotations

import argparse
import json
import math
import subprocess
import wave
from dataclasses import asdict, dataclass
from pathlib import Path

import numpy as np


@dataclass(frozen=True)
class TestMediaFixture:
    output: Path
    video_without_subtitles: Path
    subtitle_ass: Path
    voice_stem: Path
    bgm_stem: Path
    mixed_audio: Path


def _run(command: list[str]) -> None:
    subprocess.run(command, check=True)


def _write_stereo_wav(path: Path, samples: np.ndarray, sample_rate: int = 44100) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    stereo = np.repeat(samples[:, None], 2, axis=1)
    pcm = (np.clip(stereo, -0.98, 0.98) * 32767.0).astype(np.int16)
    with wave.open(str(path), "wb") as wav:
        wav.setnchannels(2)
        wav.setsampwidth(2)
        wav.setframerate(sample_rate)
        wav.writeframes(pcm.tobytes())


def _voice_samples(duration: float, sample_rate: int) -> np.ndarray:
    total = int(round(duration * sample_rate))
    samples = np.zeros(total, dtype=np.float32)
    start = int(round(0.30 * sample_rate))
    end = min(total, int(round(1.35 * sample_rate)))
    if end <= start:
        return samples
    timeline = np.arange(end - start, dtype=np.float32) / sample_rate
    tone = 0.24 * np.sin(2 * math.pi * 220.0 * timeline)
    fade = min(len(tone) // 4, int(round(0.05 * sample_rate)))
    if fade:
        tone[:fade] *= np.linspace(0.0, 1.0, fade, endpoint=False, dtype=np.float32)
        tone[-fade:] *= np.linspace(1.0, 0.0, fade, endpoint=False, dtype=np.float32)
    samples[start:end] = tone
    return samples


def _bgm_samples(duration: float, sample_rate: int, profile: str) -> np.ndarray:
    total = int(round(duration * sample_rate))
    timeline = np.arange(total, dtype=np.float32) / sample_rate
    bed = np.sin(2 * math.pi * 110.0 * timeline).astype(np.float32)
    if profile == "stable":
        envelope = np.full(total, 0.035, dtype=np.float32)
    elif profile == "ramped_bgm":
        envelope = np.linspace(0.004, 0.090, total, dtype=np.float32)
    else:
        raise ValueError(f"unsupported audio_profile: {profile}")
    return bed * envelope


def _write_subtitles(path: Path, width: int, height: int, mode: str) -> None:
    if mode == "safe":
        dialogue = "Dialogue: 0,0:00:00.40,0:00:00.95,Default,,0,0,0,,确定性字幕安全带"
    elif mode == "unsafe_content_overlap":
        dialogue = "Dialogue: 0,0:00:00.40,0:00:00.95,Unsafe,,0,0,0,,UNSAFE SUBTITLE OVER CONTENT"
    else:
        raise ValueError(f"unsupported subtitle_mode: {mode}")

    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "\n".join(
            [
                "[Script Info]",
                "ScriptType: v4.00+",
                f"PlayResX: {width}",
                f"PlayResY: {height}",
                "ScaledBorderAndShadow: yes",
                "",
                "[V4+ Styles]",
                "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding",
                "Style: Default,DejaVu Sans,48,&H00FFFFFF,&H000000FF,&H00101824,&H78000000,0,0,0,0,100,100,0,0,1,4,1,2,96,96,100,1",
                "Style: Unsafe,DejaVu Sans,80,&H00FFFFFF,&H000000FF,&H00101824,&H78000000,0,0,0,0,100,100,0,0,1,4,1,5,96,96,0,1",
                "",
                "[Events]",
                "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text",
                dialogue,
                "",
            ]
        ),
        encoding="utf-8",
    )


def _subtitle_filter_path(path: Path) -> str:
    return str(path).replace("\\", "\\\\").replace(":", "\\:").replace("'", "\\'")


def render_deterministic_media(
    destination: Path,
    *,
    duration: float = 3.0,
    width: int = 1920,
    height: int = 1080,
    fps: int = 24,
    subtitle_mode: str = "safe",
    audio_profile: str = "stable",
    black_interval: tuple[float, float] | None = None,
) -> TestMediaFixture:
    destination = Path(destination)
    destination.mkdir(parents=True, exist_ok=True)

    video_without_subtitles = destination / "video_without_subtitles.mp4"
    subtitle_ass = destination / "subtitles.ass"
    voice_stem = destination / "voice_stem.wav"
    bgm_stem = destination / "bgm_stem.wav"
    mixed_audio = destination / "mixed_audio.wav"
    output = destination / "deterministic_media.mp4"

    sample_rate = 44100
    voice = _voice_samples(duration, sample_rate)
    bgm = _bgm_samples(duration, sample_rate, audio_profile)
    mixed = voice + bgm
    peak = float(np.max(np.abs(mixed))) if len(mixed) else 0.0
    if peak > 0.96:
        mixed *= 0.96 / peak
    _write_stereo_wav(voice_stem, voice, sample_rate)
    _write_stereo_wav(bgm_stem, bgm, sample_rate)
    _write_stereo_wav(mixed_audio, mixed, sample_rate)
    _write_subtitles(subtitle_ass, width, height, subtitle_mode)

    video_filter = "format=yuv420p"
    if black_interval is not None:
        start, end = black_interval
        video_filter = (
            f"drawbox=x=0:y=0:w=iw:h=ih:color=black:t=fill:"
            f"enable='between(t,{start:.3f},{end:.3f})',format=yuv420p"
        )

    _run(
        [
            "ffmpeg",
            "-y",
            "-hide_banner",
            "-loglevel",
            "error",
            "-f",
            "lavfi",
            "-i",
            f"testsrc2=size={width}x{height}:rate={fps}:duration={duration:.3f}",
            "-vf",
            video_filter,
            "-an",
            "-c:v",
            "libx264",
            "-preset",
            "ultrafast",
            "-crf",
            "18",
            "-pix_fmt",
            "yuv420p",
            str(video_without_subtitles),
        ]
    )
    _run(
        [
            "ffmpeg",
            "-y",
            "-hide_banner",
            "-loglevel",
            "error",
            "-i",
            str(video_without_subtitles),
            "-i",
            str(mixed_audio),
            "-vf",
            f"subtitles='{_subtitle_filter_path(subtitle_ass)}'",
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
            str(sample_rate),
            "-ac",
            "2",
            "-movflags",
            "+faststart",
            str(output),
        ]
    )
    return TestMediaFixture(
        output=output,
        video_without_subtitles=video_without_subtitles,
        subtitle_ass=subtitle_ass,
        voice_stem=voice_stem,
        bgm_stem=bgm_stem,
        mixed_audio=mixed_audio,
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Render deterministic local media fixtures for offline verification."
    )
    parser.add_argument("destination", type=Path)
    parser.add_argument("--duration", type=float, default=3.0)
    parser.add_argument("--width", type=int, default=1920)
    parser.add_argument("--height", type=int, default=1080)
    parser.add_argument("--fps", type=int, default=24)
    parser.add_argument("--subtitle-mode", choices=("safe", "unsafe_content_overlap"), default="safe")
    parser.add_argument("--audio-profile", choices=("stable", "ramped_bgm"), default="stable")
    parser.add_argument("--black-start", type=float)
    parser.add_argument("--black-end", type=float)
    return parser


def main() -> int:
    args = build_parser().parse_args()
    black_interval = None
    if args.black_start is not None or args.black_end is not None:
        if args.black_start is None or args.black_end is None:
            raise SystemExit("--black-start and --black-end must be provided together")
        black_interval = (args.black_start, args.black_end)
    fixture = render_deterministic_media(
        args.destination,
        duration=args.duration,
        width=args.width,
        height=args.height,
        fps=args.fps,
        subtitle_mode=args.subtitle_mode,
        audio_profile=args.audio_profile,
        black_interval=black_interval,
    )
    print(json.dumps({key: str(value) for key, value in asdict(fixture).items()}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
