import subprocess
import tempfile
import textwrap
import unittest
from pathlib import Path

from scripts import scan_public_release as scanner


def git(repo: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", *args],
        cwd=repo,
        check=True,
        capture_output=True,
        text=True,
    )


def build_path(*parts: str) -> str:
    return "/" + "/".join(parts)


def build_private_key_block() -> str:
    begin = "-----BEGIN " + "PRIVATE KEY" + "-----"
    end = "-----END " + "PRIVATE KEY" + "-----"
    return f"{begin}\nabc\n{end}"


def build_cookie_header() -> str:
    return "Cookie" + ": " + "; ".join(["sessionid=abc123", "csrftoken=def456"])


def render_assignment(name: str, value: str) -> str:
    return f"{name} = " + '"' + value + '"'


class PublicReleaseScanTests(unittest.TestCase):
    def test_public_scan_rejects_real_token_pattern(self):
        self.assertIsNotNone(scanner.scan_text("ghp_" + "a" * 36))

    def test_public_scan_allows_placeholder(self):
        self.assertIsNone(scanner.scan_text("YOUR_API_KEY"))

    def test_public_scan_rejects_user_absolute_path(self):
        self.assertIsNotNone(scanner.scan_text(build_path("home", "alice", "private", "video.mp4")))

    def test_public_scan_rejects_openai_key(self):
        self.assertIsNotNone(scanner.scan_text("sk-proj-" + "A" * 48))

    def test_public_scan_rejects_private_key_block(self):
        self.assertIsNotNone(scanner.scan_text(build_private_key_block()))

    def test_public_scan_rejects_cookie_header(self):
        self.assertIsNotNone(scanner.scan_text(build_cookie_header()))

    def test_public_scan_rejects_common_secret_assignment_names(self):
        cases = [
            render_assignment("api_key", "abcd1234" * 4),
            render_assignment("token", "tok_" + "A" * 24),
            render_assignment("secret", "sec_" + "A" * 24),
            render_assignment("client_secret", "client_" + "A" * 24),
            render_assignment("password", "pass_" + "A" * 24),
        ]
        for value in cases:
            self.assertIsNotNone(scanner.scan_text(value))

    def test_public_scan_allows_explicit_placeholder_assignment(self):
        self.assertIsNone(scanner.scan_text(render_assignment("api_key", "YOUR_API_KEY")))
        self.assertIsNone(scanner.scan_text(render_assignment("client_secret", "YOUR_CLIENT_SECRET")))

    def test_public_scan_rejects_non_explicit_placeholder(self):
        fake_secret = "_".join(["replace", "me", "later", "secret", "value", "123456"])
        self.assertIsNotNone(scanner.scan_text(render_assignment("api_key", fake_secret)))
        self.assertIsNotNone(
            scanner.scan_text(render_assignment("secret", "_".join(["PLACEHOLDER", "SECRET", "VALUE"])))
        )

    def test_public_scan_rejects_cloud_and_service_credentials(self):
        cases = [
            "AKIA" + "A" * 16,
            "AIza" + "A" * 35,
            "xoxb-" + "1234567890-1234567890-abcdefghijklmnopqrstuv",
            "glpat-" + "A" * 20,
            "sk_live_" + "A" * 24,
            render_assignment("azure_client_secret", "A" * 32),
        ]
        for value in cases:
            self.assertIsNotNone(scanner.scan_text(value))


class RepositoryScannerTests(unittest.TestCase):
    def make_repo(self) -> Path:
        tmpdir = tempfile.TemporaryDirectory()
        self.addCleanup(tmpdir.cleanup)
        repo = Path(tmpdir.name)
        git(repo, "init")
        git(repo, "config", "user.name", "Codex Test")
        git(repo, "config", "user.email", "codex@example.com")
        (repo / ".gitignore").write_text(".superpowers/\ndocs/superpowers/\n", encoding="utf-8")
        (repo / "README.md").write_text("clean repo\n", encoding="utf-8")
        git(repo, "add", ".gitignore", "README.md")
        git(repo, "commit", "-m", "init")
        return repo

    def test_scan_repository_fails_for_tracked_sensitive_file(self):
        repo = self.make_repo()
        secret_text = render_assignment("token", "tok_" + "A" * 24) + "\n"
        (repo / "public.txt").write_text(secret_text, encoding="utf-8")
        git(repo, "add", "public.txt")
        status, findings = scanner.scan_repository(repo)
        self.assertNotEqual(status, 0)
        self.assertTrue(any("public.txt" in finding for finding in findings))

    def test_scan_repository_fails_for_staged_diff_sensitive_text(self):
        repo = self.make_repo()
        content = (repo / "README.md")
        content.write_text("safe\n" + render_assignment("api_key", "A" * 32) + "\n", encoding="utf-8")
        git(repo, "add", "README.md")
        status, findings = scanner.scan_repository(repo)
        self.assertNotEqual(status, 0)
        self.assertTrue(any("STAGED_DIFF" in finding for finding in findings))

    def test_scan_repository_fails_for_head_history_sensitive_text(self):
        repo = self.make_repo()
        secret_text = "leak " + build_path("home", "alice", "secret", "notes.txt") + "\n"
        (repo / "history.txt").write_text(secret_text, encoding="utf-8")
        git(repo, "add", "history.txt")
        git(repo, "commit", "-m", "add bad history")
        (repo / "history.txt").write_text("sanitized\n", encoding="utf-8")
        git(repo, "add", "history.txt")
        status, findings = scanner.scan_repository(repo)
        self.assertNotEqual(status, 0)
        self.assertTrue(any("GIT_HISTORY" in finding for finding in findings))

    def test_internal_superpowers_report_is_ignored_and_not_public_candidate(self):
        repo = self.make_repo()
        report = repo / ".superpowers" / "sdd" / "task-1-report.md"
        control_doc = repo / "docs" / "superpowers" / "note.md"
        report.parent.mkdir(parents=True, exist_ok=True)
        control_doc.parent.mkdir(parents=True, exist_ok=True)
        report.write_text(
            textwrap.dedent(
                """
                report
                API material
                """
            ).strip()
            + "\n"
            + render_assignment("api_key", "A" * 32)
            + "\n",
            encoding="utf-8",
        )
        control_doc.write_text("internal\n", encoding="utf-8")
        candidates = [path.relative_to(repo).as_posix() for path in scanner.iter_public_candidates(repo)]
        self.assertNotIn(".superpowers/sdd/task-1-report.md", candidates)
        self.assertNotIn("docs/superpowers/note.md", candidates)
        ignored = subprocess.run(
            ["git", "check-ignore", ".superpowers/sdd/task-1-report.md"],
            cwd=repo,
            capture_output=True,
            text=True,
        )
        self.assertEqual(ignored.returncode, 0)
        ignored_doc = subprocess.run(
            ["git", "check-ignore", "docs/superpowers/note.md"],
            cwd=repo,
            capture_output=True,
            text=True,
        )
        self.assertEqual(ignored_doc.returncode, 0)
        self.assertEqual(scanner.scan_repository(repo)[0], 0)


if __name__ == "__main__":
    unittest.main()
