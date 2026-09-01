import json
import tempfile
import unittest
from pathlib import Path

from explainer_video_editor.manifest import load_project


def card_visual(start: float = 0.0, end: float = 4.0, visual_id: str = "card-1") -> dict:
    return {
        "id": visual_id,
        "kind": "card",
        "start": start,
        "end": end,
        "card": {
            "template": "hero",
            "kicker": "公开示例",
            "title_lines": [{"text": "最小卡片"}],
        },
    }


def image_visual(source: str = "figure.png") -> dict:
    return {
        "id": "image-1",
        "kind": "image",
        "start": 0.0,
        "end": 4.0,
        "source": source,
    }


def card_pair_project() -> dict:
    return {
        "version": 2,
        "mode": "create",
        "theme": "research_ppt",
        "duration": 8.0,
        "fps": 24,
        "width": 1920,
        "height": 1080,
        "output_dir": "output",
        "output_name": "pair.mp4",
        "audio": {
            "generator": "synthetic",
            "sample_rate": 44100,
        },
        "visuals": [
            {
                "id": "image-1",
                "kind": "image",
                "start": 0.0,
                "end": 4.0,
                "source": "001-first.png",
            },
            {
                "id": "image-2",
                "kind": "image",
                "start": 4.0,
                "end": 8.0,
                "source": "002-second.png",
            },
        ],
        "narration": [
            {"id": "n1", "start": 0.0, "end_limit": 4.0, "text": "第一段旁白。"},
            {"id": "n2", "start": 4.0, "end_limit": 8.0, "text": "第二段旁白。"},
        ],
        "verification": {"frame_times": [2.0, 6.0], "duration_tolerance": 0.2},
    }


def minimal_project() -> dict:
    return {
        "version": 2,
        "mode": "create",
        "theme": "research_ppt",
        "duration": 4.0,
        "fps": 24,
        "width": 1920,
        "height": 1080,
        "output_dir": "output",
        "output_name": "demo.mp4",
        "audio": {
            "generator": "synthetic",
            "sample_rate": 44100,
        },
        "visuals": [card_visual()],
        "narration": [
            {"id": "n1", "start": 0.0, "end_limit": 4.0, "text": "这是公开 create 示例。"}
        ],
        "verification": {"frame_times": [2.0], "duration_tolerance": 0.2},
    }


def minimal_edit_project() -> dict:
    return {
        "version": 2,
        "mode": "edit",
        "theme": "research_ppt",
        "fps": 24,
        "width": 320,
        "height": 180,
        "output_dir": "output",
        "output_name": "edited.mp4",
        "sources": [
            {"id": "main", "generator": "synthetic", "duration": 3.0},
        ],
        "operations": [],
        "verification": {"frame_times": [0.5], "duration_tolerance": 0.25},
    }


class ManifestTests(unittest.TestCase):
    def write_project(self, directory: Path, payload: dict) -> Path:
        path = directory / "project.json"
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        return path

    def test_load_project_resolves_relative_output_dir(self):
        payload = minimal_project()
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            project = load_project(self.write_project(root, payload))
        self.assertEqual(project["mode"], "create")
        self.assertTrue(Path(project["output_dir"]).is_absolute())

    def test_load_project_rejects_unsupported_mode(self):
        payload = minimal_project()
        payload["mode"] = "draft"
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            with self.assertRaisesRegex(ValueError, "unsupported mode"):
                load_project(self.write_project(root, payload))

    def test_load_project_rejects_non_contiguous_timeline(self):
        payload = minimal_project()
        payload["duration"] = 6.0
        payload["visuals"] = [card_visual(0.0, 2.0, "card-1"), card_visual(3.0, 6.0, "card-2")]
        payload["narration"][0]["end_limit"] = 6.0
        payload["verification"]["frame_times"] = [1.0, 5.0]
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            with self.assertRaisesRegex(ValueError, "contiguous"):
                load_project(self.write_project(root, payload))

    def test_load_project_rejects_output_name_escape(self):
        payload = minimal_project()
        payload["output_name"] = "../escape.mp4"
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            with self.assertRaisesRegex(ValueError, "output_name"):
                load_project(self.write_project(root, payload))

    def test_load_project_rejects_edit_output_name_dot_segments(self):
        for output_name in (".", ".."):
            payload = minimal_edit_project()
            payload["output_name"] = output_name
            with tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                with self.subTest(output_name=output_name):
                    with self.assertRaisesRegex(ValueError, "output_name"):
                        load_project(self.write_project(root, payload))

    def test_load_project_resolves_relative_image_source(self):
        payload = minimal_project()
        payload["visuals"] = [image_visual()]
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "figure.png").write_bytes(b"not-a-real-image-but-path-must-resolve")
            project = load_project(self.write_project(root, payload))
        self.assertTrue(Path(project["visuals"][0]["source"]).is_absolute())

    def test_load_project_rejects_source_inside_output_directory(self):
        payload = minimal_project()
        payload["visuals"] = [image_visual("output/source.png")]
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            output_dir = root / "output"
            output_dir.mkdir()
            (output_dir / "source.png").write_bytes(b"x")
            with self.assertRaisesRegex(ValueError, "outside output_dir"):
                load_project(self.write_project(root, payload))

    def test_load_project_preserves_visual_and_narration_order(self):
        payload = card_pair_project()
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "001-first.png").write_bytes(b"first")
            (root / "002-second.png").write_bytes(b"second")
            project = load_project(self.write_project(root, payload))
        self.assertEqual([visual["id"] for visual in project["visuals"]], ["image-1", "image-2"])
        self.assertEqual(
            [Path(visual["source"]).name for visual in project["visuals"]],
            ["001-first.png", "002-second.png"],
        )
        self.assertEqual([entry["id"] for entry in project["narration"]], ["n1", "n2"])


if __name__ == "__main__":
    unittest.main()
