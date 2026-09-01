import tempfile
import unittest
import subprocess
from pathlib import Path

from scripts.render_test_media import render_deterministic_media
from scripts.verify_media import MediaVerificationError, verify_media


class MediaVerificationTests(unittest.TestCase):
    def test_deterministic_fixture_verifies_complete_media_contract(self):
        with tempfile.TemporaryDirectory() as directory:
            fixture = render_deterministic_media(Path(directory), duration=3.0)

            report = verify_media(
                output=fixture.output,
                expected_duration=3.0,
                expected_width=1920,
                expected_height=1080,
                expected_fps=24,
                subtitle_ass=fixture.subtitle_ass,
                video_without_subtitles=fixture.video_without_subtitles,
                voice_stem=fixture.voice_stem,
                bgm_stem=fixture.bgm_stem,
                subtitle_safe_y=820,
            )

        self.assertEqual(report["video"]["codec"], "h264")
        self.assertEqual((report["video"]["width"], report["video"]["height"]), (1920, 1080))
        self.assertEqual(report["audio"]["codec"], "aac")
        self.assertEqual((report["audio"]["sample_rate"], report["audio"]["channels"]), (44100, 2))
        self.assertAlmostEqual(report["duration"], 3.0, delta=0.12)
        self.assertEqual(report["black_intervals"], [])
        self.assertGreater(report["subtitle_checks"][0]["subtitle_band_pixels"], 300)
        self.assertLessEqual(report["subtitle_checks"][0]["content_pixels"], 5000)
        self.assertLess(report["audio"]["peak_db"], -0.1)
        self.assertGreater(report["audio_layers"]["voice_peak_db"], report["audio_layers"]["bgm_peak_db"])
        self.assertLessEqual(report["audio_layers"]["bgm_to_voice_peak_delta_db"], -6.0)
        self.assertLessEqual(report["audio"]["max_loudness_ramp_db"], 6.0)

    def test_verifier_rejects_subtitle_pixels_outside_safe_band(self):
        with tempfile.TemporaryDirectory() as directory:
            fixture = render_deterministic_media(
                Path(directory),
                duration=3.0,
                subtitle_mode="unsafe_content_overlap",
            )

            with self.assertRaisesRegex(MediaVerificationError, "subtitle.*safe band"):
                verify_media(
                    output=fixture.output,
                    expected_duration=3.0,
                    expected_width=1920,
                    expected_height=1080,
                    expected_fps=24,
                    subtitle_ass=fixture.subtitle_ass,
                    video_without_subtitles=fixture.video_without_subtitles,
                    voice_stem=fixture.voice_stem,
                    bgm_stem=fixture.bgm_stem,
                    subtitle_safe_y=820,
                )

    def test_verifier_checks_all_subtitle_events(self):
        with tempfile.TemporaryDirectory() as directory:
            fixture = render_deterministic_media(Path(directory), duration=3.0)
            multi = Path(directory) / "multi.ass"
            multi.write_text(
                fixture.subtitle_ass.read_text(encoding="utf-8").replace(
                    "Dialogue: 0,0:00:00.40,0:00:00.95,Default,,0,0,0,,确定性字幕安全带",
                    "Dialogue: 0,0:00:00.40,0:00:00.95,Default,,0,0,0,,确定性字幕安全带\n"
                    "Dialogue: 0,0:00:01.50,0:00:02.10,Unsafe,,0,0,0,,UNSAFE SECOND CUE",
                ),
                encoding="utf-8",
            )
            output = Path(directory) / "multi-subtitle.mp4"
            subprocess.run(
                [
                    "ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
                    "-i", str(fixture.video_without_subtitles), "-i", str(fixture.mixed_audio),
                    "-vf", f"subtitles='{str(multi).replace(':', '\\:')}'",
                    "-map", "0:v:0", "-map", "1:a:0", "-t", "3.000",
                    "-c:v", "libx264", "-preset", "ultrafast", "-crf", "18", "-pix_fmt", "yuv420p",
                    "-c:a", "aac", "-ar", "44100", "-ac", "2", str(output),
                ],
                check=True,
            )
            with self.assertRaisesRegex(MediaVerificationError, "subtitle.*safe band"):
                verify_media(
                    output=output,
                    expected_duration=3.0,
                    expected_width=1920,
                    expected_height=1080,
                    expected_fps=24,
                    subtitle_ass=multi,
                    video_without_subtitles=fixture.video_without_subtitles,
                    subtitle_safe_y=820,
                )

    def test_verifier_rejects_loudness_ramp(self):
        with tempfile.TemporaryDirectory() as directory:
            fixture = render_deterministic_media(
                Path(directory),
                duration=3.0,
                audio_profile="ramped_bgm",
            )

            with self.assertRaisesRegex(MediaVerificationError, "loudness ramp"):
                verify_media(
                    output=fixture.output,
                    expected_duration=3.0,
                    expected_width=1920,
                    expected_height=1080,
                    expected_fps=24,
                    subtitle_ass=fixture.subtitle_ass,
                    video_without_subtitles=fixture.video_without_subtitles,
                    voice_stem=fixture.voice_stem,
                    bgm_stem=fixture.bgm_stem,
                    subtitle_safe_y=820,
                )

    def test_verifier_rejects_output_with_missing_audio_layers(self):
        with tempfile.TemporaryDirectory() as directory:
            fixture = render_deterministic_media(Path(directory), duration=3.0)
            silent = Path(directory) / "silent.mp4"
            subprocess.run(
                ["ffmpeg", "-y", "-hide_banner", "-loglevel", "error", "-i", str(fixture.output), "-c:v", "copy", "-an", str(silent)],
                check=True,
            )
            with self.assertRaisesRegex(Exception, "audio metadata mismatch|voice stem"):
                verify_media(
                    output=silent,
                    expected_duration=3.0,
                    expected_width=1920,
                    expected_height=1080,
                    expected_fps=24,
                    voice_stem=fixture.voice_stem,
                    bgm_stem=fixture.bgm_stem,
                )

    def test_verifier_rejects_short_black_frame(self):
        with tempfile.TemporaryDirectory() as directory:
            fixture = render_deterministic_media(Path(directory), duration=3.0, black_interval=(1.0, 1.10))
            with self.assertRaisesRegex(MediaVerificationError, "black frames"):
                verify_media(
                    output=fixture.output,
                    expected_duration=3.0,
                    expected_width=1920,
                    expected_height=1080,
                    expected_fps=24,
                    voice_stem=fixture.voice_stem,
                    bgm_stem=fixture.bgm_stem,
                )

    def test_verifier_detects_black_frame_regression(self):
        with tempfile.TemporaryDirectory() as directory:
            fixture = render_deterministic_media(
                Path(directory),
                duration=3.0,
                black_interval=(1.0, 1.35),
            )

            with self.assertRaisesRegex(MediaVerificationError, "black frames"):
                verify_media(
                    output=fixture.output,
                    expected_duration=3.0,
                    expected_width=1920,
                    expected_height=1080,
                    expected_fps=24,
                    subtitle_ass=fixture.subtitle_ass,
                    video_without_subtitles=fixture.video_without_subtitles,
                    voice_stem=fixture.voice_stem,
                    bgm_stem=fixture.bgm_stem,
                    subtitle_safe_y=820,
                )


if __name__ == "__main__":
    unittest.main()
