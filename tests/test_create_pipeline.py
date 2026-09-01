import subprocess
import tempfile
import unittest
from pathlib import Path

from explainer_video_editor.builder import build
from explainer_video_editor.manifest import load_project
from explainer_video_editor.verify import verify_project


ROOT = Path(__file__).resolve().parents[1]
EXAMPLE_PROJECT = ROOT / "examples" / "create_minimal" / "project.json"


class CreatePipelineTests(unittest.TestCase):
    def write_temp_project(self, directory: Path) -> Path:
        project_path = directory / "project.json"
        project_path.write_text(
            EXAMPLE_PROJECT.read_text(encoding="utf-8").replace('"output"', '"build-output"', 1),
            encoding="utf-8",
        )
        return project_path

    def test_public_example_build_and_verify_roundtrip(self):
        with tempfile.TemporaryDirectory() as directory:
            temp_root = Path(directory)
            project_path = self.write_temp_project(temp_root)
            project = load_project(project_path)
            output = build(project)
            report = verify_project(project)
            self.assertTrue(output.is_file())
            self.assertEqual(Path(report["output"]), output)
            self.assertTrue((output.parent / "work" / "subtitles.ass").is_file())
            self.assertGreater(report["duration"], 0.0)
            ffprobe = subprocess.run(
                [
                    "ffprobe",
                    "-v",
                    "error",
                    "-show_entries",
                    "format=duration",
                    "-of",
                    "default=noprint_wrappers=1:nokey=1",
                    str(output),
                ],
                capture_output=True,
                text=True,
                check=True,
            )
            duration = float(ffprobe.stdout.strip())
            self.assertAlmostEqual(duration, float(project["duration"]), delta=0.1)
            self.assertAlmostEqual(report["duration"], float(project["duration"]), delta=0.1)

    def test_cli_validate_reports_create_mode_for_public_example(self):
        result = subprocess.run(
            [
                "python3",
                "-m",
                "explainer_video_editor.cli",
                "validate",
                str(EXAMPLE_PROJECT),
            ],
            cwd=ROOT,
            capture_output=True,
            text=True,
            check=True,
        )
        self.assertIn("mode=create", result.stdout)
        self.assertIn("duration=", result.stdout)

    def test_cli_validate_rejects_missing_manifest_with_nonzero_exit(self):
        missing = ROOT / "examples" / "create_minimal" / "missing-project.json"
        result = subprocess.run(
            [
                "python3",
                "-m",
                "explainer_video_editor.cli",
                "validate",
                str(missing),
            ],
            cwd=ROOT,
            capture_output=True,
            text=True,
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("ERROR:", result.stderr)
        self.assertIn("missing-project.json", result.stderr)
        self.assertNotIn("Traceback", result.stderr)

    def test_cli_validate_rejects_invalid_manifest_with_nonzero_exit(self):
        with tempfile.TemporaryDirectory() as directory:
            temp_root = Path(directory)
            project_path = temp_root / "invalid-project.json"
            project_path.write_text(
                EXAMPLE_PROJECT.read_text(encoding="utf-8").replace('"create"', '"draft"', 1),
                encoding="utf-8",
            )
            result = subprocess.run(
                [
                    "python3",
                    "-m",
                    "explainer_video_editor.cli",
                    "validate",
                    str(project_path),
                ],
                cwd=ROOT,
                capture_output=True,
                text=True,
            )
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("ERROR:", result.stderr)
        self.assertIn("unsupported mode: draft", result.stderr)
        self.assertNotIn("Traceback", result.stderr)

    def test_cli_build_returns_zero_for_temp_project(self):
        with tempfile.TemporaryDirectory() as directory:
            temp_root = Path(directory)
            project_path = self.write_temp_project(temp_root)
            result = subprocess.run(
                [
                    "python3",
                    "-m",
                    "explainer_video_editor.cli",
                    "build",
                    str(project_path),
                ],
                cwd=ROOT,
                capture_output=True,
                text=True,
            )
            self.assertEqual(result.returncode, 0)
            self.assertIn("成片输出：", result.stdout)
            self.assertTrue((temp_root / "build-output" / "public-create-demo.mp4").is_file())

    def test_cli_verify_returns_zero_for_temp_project(self):
        with tempfile.TemporaryDirectory() as directory:
            temp_root = Path(directory)
            project_path = self.write_temp_project(temp_root)
            build_result = subprocess.run(
                [
                    "python3",
                    "-m",
                    "explainer_video_editor.cli",
                    "build",
                    str(project_path),
                ],
                cwd=ROOT,
                capture_output=True,
                text=True,
            )
            self.assertEqual(build_result.returncode, 0)
            result = subprocess.run(
                [
                    "python3",
                    "-m",
                    "explainer_video_editor.cli",
                    "verify",
                    str(project_path),
                ],
                cwd=ROOT,
                capture_output=True,
                text=True,
            )
            self.assertEqual(result.returncode, 0)
            self.assertIn('"duration"', result.stdout)
            self.assertIn('"output"', result.stdout)

    def test_cli_verify_rejects_missing_output_with_nonzero_exit(self):
        with tempfile.TemporaryDirectory() as directory:
            temp_root = Path(directory)
            project_path = self.write_temp_project(temp_root)
            result = subprocess.run(
                [
                    "python3",
                    "-m",
                    "explainer_video_editor.cli",
                    "verify",
                    str(project_path),
                ],
                cwd=ROOT,
                capture_output=True,
                text=True,
            )
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("ERROR:", result.stderr)
            self.assertIn("public-create-demo.mp4", result.stderr)
            self.assertNotIn("Traceback", result.stderr)


if __name__ == "__main__":
    unittest.main()
