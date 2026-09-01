from __future__ import annotations

import argparse
import json
import math
import re
import subprocess
import tempfile
from fractions import Fraction
from pathlib import Path
from typing import Any

import numpy as np
from PIL import Image


class MediaVerificationError(RuntimeError):
    pass


def _run(command: list[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(command, check=True, capture_output=True, text=True)


def _stream(metadata: dict[str, Any], kind: str) -> dict[str, Any] | None:
    return next((item for item in metadata.get("streams", []) if item.get("codec_type") == kind), None)


def _probe(path: Path) -> dict[str, Any]:
    return json.loads(
        _run(
            [
                "ffprobe",
                "-v",
                "error",
                "-show_entries",
                "format=duration,size:stream=codec_name,codec_type,width,height,r_frame_rate,sample_rate,channels",
                "-of",
                "json",
                str(path),
            ]
        ).stdout
    )


def _assert_playable(path: Path) -> None:
    try:
        _run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-i", str(path), "-f", "null", "-"])
    except subprocess.CalledProcessError as exc:
        raise MediaVerificationError(f"media is not playable: {path}") from exc


def _decode_audio(path: Path, sample_rate: int = 44100, channels: int = 2) -> np.ndarray:
    result = subprocess.run(
        [
            "ffmpeg",
            "-hide_banner",
            "-loglevel",
            "error",
            "-i",
            str(path),
            "-f",
            "s16le",
            "-acodec",
            "pcm_s16le",
            "-ar",
            str(sample_rate),
            "-ac",
            str(channels),
            "-",
        ],
        check=True,
        stdout=subprocess.PIPE,
    )
    raw = np.frombuffer(result.stdout, dtype=np.int16).astype(np.float32) / 32768.0
    if raw.size == 0:
        return np.zeros((0, channels), dtype=np.float32)
    return raw.reshape(-1, channels)


def _peak_db(samples: np.ndarray) -> float:
    peak = float(np.max(np.abs(samples))) if samples.size else 0.0
    if peak <= 0.0:
        return -math.inf
    return 20.0 * math.log10(peak)


def _rms_db(samples: np.ndarray) -> float:
    if samples.size == 0:
        return -math.inf
    rms = float(np.sqrt(np.mean(np.square(samples), dtype=np.float64)))
    if rms <= 0.0:
        return -math.inf
    return 20.0 * math.log10(rms)


def _max_positive_loudness_ramp_db(samples: np.ndarray, sample_rate: int = 44100) -> float:
    if samples.size == 0:
        return 0.0
    mono = samples.mean(axis=1)
    window = max(1, int(round(sample_rate * 0.50)))
    levels: list[float] = []
    for start in range(0, len(mono), window):
        level = _rms_db(mono[start : start + window])
        if math.isfinite(level):
            levels.append(level)
    if len(levels) < 2:
        return 0.0
    return max(0.0, max(later - earlier for earlier, later in zip(levels, levels[1:])))


def _parse_ass_time(value: str) -> float:
    hours, minutes, seconds = value.split(":")
    return int(hours) * 3600 + int(minutes) * 60 + float(seconds)


def _ass_dialogue_midpoints(path: Path) -> list[float]:
    midpoints: list[float] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.startswith("Dialogue:"):
            continue
        fields = line.split(",", 9)
        if len(fields) < 10:
            continue
        start = _parse_ass_time(fields[1])
        end = _parse_ass_time(fields[2])
        if end > start:
            midpoints.append((start + end) / 2.0)
    if not midpoints:
        raise MediaVerificationError(f"subtitle file has no dialogue cues: {path}")
    return midpoints


def _extract_frame(video: Path, timestamp: float, destination: Path) -> None:
    subprocess.run(
        [
            "ffmpeg",
            "-y",
            "-hide_banner",
            "-loglevel",
            "error",
            "-ss",
            f"{timestamp:.3f}",
            "-i",
            str(video),
            "-frames:v",
            "1",
            str(destination),
        ],
        check=True,
    )
    if not destination.is_file() or destination.stat().st_size == 0:
        raise MediaVerificationError(f"failed to extract frame at {timestamp:.3f}s from {video}")


def _check_subtitles(
    output: Path,
    reference: Path,
    subtitle_ass: Path,
    subtitle_safe_y: int,
    pixel_threshold: int = 20,
    minimum_subtitle_pixels: int = 300,
    maximum_content_pixels: int = 5000,
) -> list[dict[str, int]]:
    checks: list[dict[str, int]] = []
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        for index, timestamp in enumerate(_ass_dialogue_midpoints(subtitle_ass)):
            final_frame = root / f"final_{index}.png"
            reference_frame = root / f"reference_{index}.png"
            _extract_frame(output, timestamp, final_frame)
            _extract_frame(reference, timestamp, reference_frame)
            final = np.asarray(Image.open(final_frame).convert("RGB"), dtype=np.int16)
            base = np.asarray(Image.open(reference_frame).convert("RGB"), dtype=np.int16)
            if final.shape != base.shape:
                raise MediaVerificationError(f"subtitle frame shape mismatch: {final.shape} != {base.shape}")
            difference = np.max(np.abs(final - base), axis=2)
            content_pixels = int(np.count_nonzero(difference[:subtitle_safe_y] > pixel_threshold))
            subtitle_band_pixels = int(np.count_nonzero(difference[subtitle_safe_y:] > pixel_threshold))
            if subtitle_band_pixels <= minimum_subtitle_pixels:
                raise MediaVerificationError(
                    f"subtitle did not render in safe band: changed pixels={subtitle_band_pixels}"
                )
            if content_pixels > maximum_content_pixels:
                raise MediaVerificationError(
                    f"subtitle violates safe band: changed pixels above band={content_pixels}"
                )
            checks.append(
                {
                    "timestamp_ms": int(round(timestamp * 1000)),
                    "content_pixels": content_pixels,
                    "subtitle_band_pixels": subtitle_band_pixels,
                }
            )
    return checks


def _black_intervals(path: Path) -> list[str]:
    result = subprocess.run(
        [
            "ffmpeg",
            "-hide_banner",
            "-v",
            "info",
            "-i",
            str(path),
            "-vf",
            "blackdetect=d=0.04:pix_th=0.10",
            "-an",
            "-f",
            "null",
            "-",
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    return re.findall(r"black_start:[^\n]+", result.stderr)


def verify_media(
    *,
    output: Path,
    expected_duration: float,
    expected_width: int,
    expected_height: int,
    expected_fps: int | None = None,
    duration_tolerance: float = 0.15,
    subtitle_ass: Path | None = None,
    video_without_subtitles: Path | None = None,
    voice_stem: Path | None = None,
    bgm_stem: Path | None = None,
    subtitle_safe_y: int | None = None,
    max_peak_db: float = -0.1,
    max_loudness_ramp_db: float = 6.0,
) -> dict[str, Any]:
    output = Path(output)
    if not output.is_file():
        raise FileNotFoundError(output)
    _assert_playable(output)
    metadata = _probe(output)
    duration = float(metadata["format"]["duration"])
    if abs(duration - expected_duration) > duration_tolerance:
        raise MediaVerificationError(
            f"duration mismatch: expected {expected_duration:.3f}, got {duration:.3f}"
        )

    video = _stream(metadata, "video")
    audio = _stream(metadata, "audio")
    actual_video = None if video is None else (video.get("codec_name"), video.get("width"), video.get("height"))
    expected_video = ("h264", expected_width, expected_height)
    if actual_video != expected_video:
        raise MediaVerificationError(f"video metadata mismatch: expected {expected_video}, got {actual_video}")
    if expected_fps is not None and Fraction(video.get("r_frame_rate", "0/1")) != Fraction(expected_fps, 1):
        raise MediaVerificationError(f"frame rate mismatch: expected {expected_fps}, got {video.get('r_frame_rate')}")

    actual_audio = None if audio is None else (
        audio.get("codec_name"),
        int(audio.get("sample_rate", 0)),
        audio.get("channels"),
    )
    expected_audio = ("aac", 44100, 2)
    if actual_audio != expected_audio:
        raise MediaVerificationError(f"audio metadata mismatch: expected {expected_audio}, got {actual_audio}")

    black_intervals = _black_intervals(output)
    if black_intervals:
        raise MediaVerificationError(f"black frames detected: {black_intervals}")

    output_audio = _decode_audio(output)
    peak_db = _peak_db(output_audio)
    if peak_db > max_peak_db:
        raise MediaVerificationError(f"audio clipping risk: peak_db={peak_db:.2f}")

    ramp_source = _decode_audio(bgm_stem) if bgm_stem is not None else output_audio
    loudness_ramp_db = _max_positive_loudness_ramp_db(ramp_source)
    if loudness_ramp_db > max_loudness_ramp_db:
        raise MediaVerificationError(f"loudness ramp detected: +{loudness_ramp_db:.2f} dB")

    subtitle_checks: list[dict[str, int]] = []
    if subtitle_ass is not None or video_without_subtitles is not None:
        if subtitle_ass is None or video_without_subtitles is None or subtitle_safe_y is None:
            raise MediaVerificationError("subtitle safe-band verification requires subtitle_ass, reference video, and safe y")
        subtitle_checks = _check_subtitles(
            output,
            Path(video_without_subtitles),
            Path(subtitle_ass),
            int(subtitle_safe_y),
        )

    audio_layers: dict[str, float] = {}
    if voice_stem is not None or bgm_stem is not None:
        if voice_stem is None or bgm_stem is None:
            raise MediaVerificationError("audio layer verification requires both voice_stem and bgm_stem")
        voice_samples = _decode_audio(Path(voice_stem))
        bgm_samples = _decode_audio(Path(bgm_stem))
        voice_peak_db = _peak_db(voice_samples)
        bgm_peak_db = _peak_db(bgm_samples)
        delta = bgm_peak_db - voice_peak_db
        if not math.isfinite(voice_peak_db) or not math.isfinite(bgm_peak_db):
            raise MediaVerificationError("voice/BGM layer is silent")
        length = min(len(output_audio), len(voice_samples), len(bgm_samples))
        if length == 0:
            raise MediaVerificationError("voice/BGM layer has no samples")
        output_mono = output_audio[:length].mean(axis=1)
        voice_mono = voice_samples[:length].mean(axis=1)
        bgm_mono = bgm_samples[:length].mean(axis=1)
        voice_active = np.abs(voice_mono) > 0.01
        if not np.any(voice_active) or _rms_db(output_mono[voice_active]) < _rms_db(voice_mono[voice_active]) - 18.0:
            raise MediaVerificationError("voice stem is not present in output audio")
        bgm_active = np.abs(bgm_mono) > 0.005
        if not np.any(bgm_active) or _rms_db(output_mono[bgm_active]) < _rms_db(bgm_mono[bgm_active]) - 18.0:
            raise MediaVerificationError("BGM stem is not present in output audio")
        if delta > -6.0:
            raise MediaVerificationError(
                f"BGM voice layer mismatch: bgm peak must be at least 6 dB below voice, got {delta:.2f} dB"
            )
        audio_layers = {
            "voice_peak_db": voice_peak_db,
            "bgm_peak_db": bgm_peak_db,
            "bgm_to_voice_peak_delta_db": delta,
        }

    return {
        "output": str(output),
        "duration": duration,
        "video": {
            "codec": video["codec_name"],
            "width": video["width"],
            "height": video["height"],
            "fps": video["r_frame_rate"],
        },
        "audio": {
            "codec": audio["codec_name"],
            "sample_rate": int(audio["sample_rate"]),
            "channels": audio["channels"],
            "peak_db": peak_db,
            "max_loudness_ramp_db": loudness_ramp_db,
        },
        "audio_layers": audio_layers,
        "black_intervals": black_intervals,
        "subtitle_checks": subtitle_checks,
        "metadata": metadata,
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Verify generated MP4 media with deterministic ffprobe/frame/audio checks.")
    parser.add_argument("output", type=Path)
    parser.add_argument("--expected-duration", type=float, required=True)
    parser.add_argument("--expected-width", type=int, required=True)
    parser.add_argument("--expected-height", type=int, required=True)
    parser.add_argument("--expected-fps", type=int)
    parser.add_argument("--duration-tolerance", type=float, default=0.15)
    parser.add_argument("--subtitle-ass", type=Path)
    parser.add_argument("--video-without-subtitles", type=Path)
    parser.add_argument("--voice-stem", type=Path)
    parser.add_argument("--bgm-stem", type=Path)
    parser.add_argument("--subtitle-safe-y", type=int)
    return parser


def main() -> int:
    args = build_parser().parse_args()
    try:
        report = verify_media(
            output=args.output,
            expected_duration=args.expected_duration,
            expected_width=args.expected_width,
            expected_height=args.expected_height,
            expected_fps=args.expected_fps,
            duration_tolerance=args.duration_tolerance,
            subtitle_ass=args.subtitle_ass,
            video_without_subtitles=args.video_without_subtitles,
            voice_stem=args.voice_stem,
            bgm_stem=args.bgm_stem,
            subtitle_safe_y=args.subtitle_safe_y,
        )
    except Exception as exc:
        print(f"MEDIA_VERIFY_FAILED: {exc}", file=subprocess.sys.stderr)
        return 1
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
