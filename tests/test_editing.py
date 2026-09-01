import json
import subprocess
import tempfile
import unittest
from pathlib import Path

from explainer_video_editor.editing import build_edit, verify_edit_project
from explainer_video_editor.manifest import load_project


ROOT = Path(__file__).resolve().parents[1]


def write_project(root: Path, payload: dict) -> Path:
    path = root / "project.json"
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def edit_project_payload() -> dict:
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
        "operations": [
            {"type": "cut", "source": "main", "start": 1.0, "end": 1.5, "authorized": True},
            {"type": "compress", "source": "main", "start": 2.0, "end": 3.0, "factor": 2.0, "authorized": True},
            {"type": "zoom", "source": "main", "start": 0.2, "end": 0.8, "zoom": 1.08},
            {
                "type": "callout",
                "start": 0.3,
                "end": 1.2,
                "text": "safe callout",
                "x": 12,
                "y": 18,
                "width": 140,
                "height": 36,
            },
        ],
        "verification": {"frame_times": [0.5, 1.5], "duration_tolerance": 0.25},
    }


class EditAuthorizationTests(unittest.TestCase):
    def test_load_project_rejects_unauthorized_destructive_cut(self):
        payload = edit_project_payload()
        payload["operations"][0]["authorized"] = False
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaisesRegex(PermissionError, "explicit authorization"):
                load_project(write_project(Path(directory), payload))

    def test_load_project_rejects_unauthorized_lossy_compress(self):
        payload = edit_project_payload()
        payload["operations"][1]["authorized"] = False
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaisesRegex(PermissionError, "explicit authorization"):
                load_project(write_project(Path(directory), payload))

    def test_load_project_rejects_unauthorized_reorder(self):
        payload = edit_project_payload()
        payload["operations"] = [
            {
                "type": "reorder",
                "source": "main",
                "authorized": False,
                "ranges": [{"start": 1.5, "end": 3.0}, {"start": 0.0, "end": 1.5}],
            }
        ]
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaisesRegex(PermissionError, "explicit authorization"):
                load_project(write_project(Path(directory), payload))


class EditBuildTests(unittest.TestCase):
    def test_build_edit_outputs_playable_h264_aac_mp4(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            project = load_project(write_project(root, edit_project_payload()))
            output = build_edit(project)
            report = verify_edit_project(project)

            self.assertTrue(Path(report["output"]).name.endswith(".mp4"))
            self.assertTrue(output.is_file())
            self.assertAlmostEqual(report["duration"], 2.0, delta=0.25)
            video = next(stream for stream in report["metadata"]["streams"] if stream["codec_type"] == "video")
            audio = next(stream for stream in report["metadata"]["streams"] if stream["codec_type"] == "audio")
            self.assertEqual((video["codec_name"], video["width"], video["height"]), ("h264", 320, 180))
            self.assertEqual((audio["codec_name"], int(audio["sample_rate"]), audio["channels"]), ("aac", 44100, 2))

    def test_edit_cli_validate_build_and_verify(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            project_path = write_project(root, edit_project_payload())
            validate = subprocess.run(
                ["python3", "-m", "explainer_video_editor.cli", "validate", str(project_path)],
                cwd=ROOT,
                capture_output=True,
                text=True,
            )
            build = subprocess.run(
                ["python3", "-m", "explainer_video_editor.cli", "build", str(project_path)],
                cwd=ROOT,
                capture_output=True,
                text=True,
            )
            verify = subprocess.run(
                ["python3", "-m", "explainer_video_editor.cli", "verify", str(project_path)],
                cwd=ROOT,
                capture_output=True,
                text=True,
            )

        self.assertEqual(validate.returncode, 0, validate.stderr)
        self.assertIn("mode=edit", validate.stdout)
        self.assertEqual(build.returncode, 0, build.stderr)
        self.assertIn("成片输出：", build.stdout)
        self.assertEqual(verify.returncode, 0, verify.stderr)
        self.assertIn('"duration"', verify.stdout)


if __name__ == "__main__":
    unittest.main()
