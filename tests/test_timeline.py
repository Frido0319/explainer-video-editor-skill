import json
import tempfile
import unittest
from pathlib import Path

from explainer_video_editor.editing import compile_edit_timeline
from explainer_video_editor.timeline import load_timeline


def write_timeline(root: Path, payload: dict) -> Path:
    path = root / "timeline.json"
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def base_timeline() -> dict:
    return {
        "version": 2,
        "mode": "edit",
        "fps": 24,
        "width": 320,
        "height": 180,
        "output_dir": "output",
        "output_name": "edited.mp4",
        "sources": [
            {"id": "main", "path": "source.mp4", "duration": 6.0},
        ],
        "operations": [],
        "verification": {"frame_times": [0.5, 2.5], "duration_tolerance": 0.25},
    }


class TimelineLoadTests(unittest.TestCase):
    def test_load_timeline_rejects_dot_output_name(self):
        for output_name in (".", ".."):
            payload = base_timeline()
            payload["output_name"] = output_name
            with tempfile.TemporaryDirectory() as directory:
                with self.subTest(output_name=output_name):
                    with self.assertRaisesRegex(ValueError, "output_name"):
                        load_timeline(write_timeline(Path(directory), payload))

    def test_load_timeline_rejects_unknown_source_references(self):
        payload = base_timeline()
        payload["operations"] = [
            {"type": "cut", "source": "missing", "start": 1.0, "end": 2.0, "authorized": True}
        ]
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaisesRegex(ValueError, "unknown source"):
                load_timeline(write_timeline(Path(directory), payload))

    def test_load_timeline_rejects_overlapping_source_operations(self):
        payload = base_timeline()
        payload["operations"] = [
            {"type": "cut", "source": "main", "start": 1.0, "end": 3.0, "authorized": True},
            {"type": "compress", "source": "main", "start": 2.5, "end": 4.0, "factor": 2.0, "authorized": True},
        ]
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaisesRegex(ValueError, "overlap"):
                load_timeline(write_timeline(Path(directory), payload))

    def test_load_timeline_caps_zoom_at_1_08x(self):
        payload = base_timeline()
        payload["operations"] = [
            {"type": "zoom", "source": "main", "start": 0.5, "end": 1.5, "zoom": 1.09}
        ]
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaisesRegex(ValueError, "1.08"):
                load_timeline(write_timeline(Path(directory), payload))

    def test_load_timeline_keeps_callouts_above_subtitle_safe_band(self):
        payload = base_timeline()
        payload["width"] = 1920
        payload["height"] = 1080
        payload["operations"] = [
            {
                "type": "callout",
                "start": 0.5,
                "end": 2.0,
                "text": "Too low",
                "x": 80,
                "y": 800,
                "width": 300,
                "height": 40,
            }
        ]
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaisesRegex(ValueError, "subtitle safe"):
                load_timeline(write_timeline(Path(directory), payload))


class EditTimelineCompilerTests(unittest.TestCase):
    def test_explicit_keep_retains_only_declared_source_ranges(self):
        payload = base_timeline()
        payload["operations"] = [
            {"type": "keep", "source": "main", "start": 1.0, "end": 2.5},
            {"type": "keep", "source": "main", "start": 4.0, "end": 5.0},
        ]
        with tempfile.TemporaryDirectory() as directory:
            timeline = load_timeline(write_timeline(Path(directory), payload))

        compiled = compile_edit_timeline(timeline)

        self.assertEqual(
            [
                (segment.source_start, segment.source_end, segment.output_start, segment.output_end, segment.speed)
                for segment in compiled.segments
            ],
            [(1.0, 2.5, 0.0, 1.5, 1.0), (4.0, 5.0, 1.5, 2.5, 1.0)],
        )
        self.assertAlmostEqual(compiled.duration, 2.5)

    def test_cut_and_compress_preserve_all_unlisted_source_ranges(self):
        payload = base_timeline()
        payload["operations"] = [
            {"type": "cut", "source": "main", "start": 2.0, "end": 3.0, "authorized": True},
            {"type": "compress", "source": "main", "start": 3.0, "end": 5.0, "factor": 2.0, "authorized": True},
        ]
        with tempfile.TemporaryDirectory() as directory:
            timeline = load_timeline(write_timeline(Path(directory), payload))

        compiled = compile_edit_timeline(timeline)

        self.assertEqual(
            [
                (segment.source, segment.source_start, segment.source_end, segment.output_start, segment.output_end, segment.speed)
                for segment in compiled.segments
            ],
            [
                ("main", 0.0, 2.0, 0.0, 2.0, 1.0),
                ("main", 3.0, 5.0, 2.0, 3.0, 2.0),
                ("main", 5.0, 6.0, 3.0, 4.0, 1.0),
            ],
        )
        self.assertAlmostEqual(compiled.duration, 4.0)

    def test_reorder_uses_the_explicit_declared_sequence_and_keeps_unlisted_ranges(self):
        payload = base_timeline()
        payload["operations"] = [
            {
                "type": "reorder",
                "source": "main",
                "authorized": True,
                "ranges": [
                    {"start": 4.0, "end": 5.0},
                    {"start": 1.0, "end": 2.0},
                ],
            }
        ]
        with tempfile.TemporaryDirectory() as directory:
            timeline = load_timeline(write_timeline(Path(directory), payload))

        compiled = compile_edit_timeline(timeline)

        self.assertEqual(
            [
                (segment.source_start, segment.source_end, segment.output_start, segment.output_end)
                for segment in compiled.segments
            ],
            [
                (4.0, 5.0, 0.0, 1.0),
                (1.0, 2.0, 1.0, 2.0),
                (0.0, 1.0, 2.0, 3.0),
                (2.0, 4.0, 3.0, 5.0),
                (5.0, 6.0, 5.0, 6.0),
            ],
        )
        self.assertAlmostEqual(compiled.duration, 6.0)

    def test_reorder_cannot_bypass_cut_and_compress_operations(self):
        payload = base_timeline()
        payload["operations"] = [
            {"type": "cut", "source": "main", "start": 1.0, "end": 2.0, "authorized": True},
            {"type": "compress", "source": "main", "start": 4.0, "end": 5.0, "factor": 2.0, "authorized": True},
            {
                "type": "reorder",
                "source": "main",
                "authorized": True,
                "ranges": [
                    {"start": 4.0, "end": 6.0},
                    {"start": 0.0, "end": 2.0},
                ],
            },
        ]
        with tempfile.TemporaryDirectory() as directory:
            timeline = load_timeline(write_timeline(Path(directory), payload))

        compiled = compile_edit_timeline(timeline)

        self.assertEqual(
            [
                (segment.source_start, segment.source_end, segment.output_start, segment.output_end, segment.speed)
                for segment in compiled.segments
            ],
            [
                (4.0, 5.0, 0.0, 0.5, 2.0),
                (5.0, 6.0, 0.5, 1.5, 1.0),
                (0.0, 1.0, 1.5, 2.5, 1.0),
                (2.0, 4.0, 2.5, 4.5, 1.0),
            ],
        )
        self.assertAlmostEqual(compiled.duration, 4.5)

    def test_subtitle_rebase_maps_retained_events_to_compiled_timeline(self):
        payload = base_timeline()
        payload["operations"] = [
            {"type": "cut", "source": "main", "start": 2.0, "end": 3.0, "authorized": True},
            {"type": "compress", "source": "main", "start": 3.0, "end": 5.0, "factor": 2.0, "authorized": True},
            {"type": "subtitle_rebase", "source": "main", "path": "captions.ass"},
        ]
        ass_text = """[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
Dialogue: 0,0:00:02.10,0:00:02.50,Default,,0,0,0,,cut away
Dialogue: 0,0:00:03.50,0:00:04.50,Default,,0,0,0,,compressed
Dialogue: 0,0:00:05.20,0:00:05.60,Default,,0,0,0,,after
"""
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "captions.ass").write_text(ass_text, encoding="utf-8")
            timeline = load_timeline(write_timeline(root, payload))
            compiled = compile_edit_timeline(timeline)

        self.assertEqual([event.text for event in compiled.subtitles], ["compressed", "after"])
        self.assertAlmostEqual(compiled.subtitles[0].start, 2.25)
        self.assertAlmostEqual(compiled.subtitles[0].end, 2.75)
        self.assertAlmostEqual(compiled.subtitles[1].start, 3.2)
        self.assertAlmostEqual(compiled.subtitles[1].end, 3.6)


if __name__ == "__main__":
    unittest.main()
