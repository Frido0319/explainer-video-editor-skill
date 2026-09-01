import json
import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
SKILL_PATH = REPO_ROOT / "SKILL.md"
README_PATH = REPO_ROOT / "README.md"
EVALS_PATH = REPO_ROOT / "evals" / "evals.json"


class SkillContractTests(unittest.TestCase):
    def test_skill_entrypoint_exists(self):
        self.assertTrue(SKILL_PATH.exists(), "SKILL.md must exist")

    def test_skill_routes_only_create_and_edit_modes(self):
        content = SKILL_PATH.read_text(encoding="utf-8")
        self.assertIn("mode=create", content)
        self.assertIn("mode=edit", content)
        self.assertNotIn("V1", content)
        self.assertNotIn("V2", content)
        self.assertNotIn("menu", content.lower())

    def test_skill_mentions_required_cli_contract(self):
        content = SKILL_PATH.read_text(encoding="utf-8")
        self.assertIn("python3 -m explainer_video_editor.cli validate", content)
        self.assertIn("python3 -m explainer_video_editor.cli build", content)
        self.assertIn("python3 -m explainer_video_editor.cli verify", content)

    def test_skill_declares_authorization_boundaries(self):
        content = SKILL_PATH.read_text(encoding="utf-8")
        self.assertIn("删除", content)
        self.assertIn("重排", content)
        self.assertIn("压缩", content)
        self.assertIn("显式授权", content)
        self.assertIn("已有视频编辑", content)
        self.assertIn("从素材制作", content)

    def test_skill_requires_visual_verification_and_security_scan(self):
        content = SKILL_PATH.read_text(encoding="utf-8")
        self.assertIn("视觉验收", content)
        self.assertIn("安全扫描", content)
        self.assertIn("scripts/scan_public_release.py", content)

    def test_readme_describes_create_and_edit_without_v1_v2(self):
        content = README_PATH.read_text(encoding="utf-8")
        self.assertIn("create", content)
        self.assertIn("edit", content)
        self.assertIn("已有视频编辑", content)
        self.assertIn("从素材制作", content)
        self.assertNotIn("V1", content)
        self.assertNotIn("V2", content)

    def test_evals_cover_required_prompt_scenarios(self):
        payload = json.loads(EVALS_PATH.read_text(encoding="utf-8"))
        prompts = payload["prompts"]
        self.assertGreaterEqual(len(prompts), 6)
        tags = {tag for prompt in prompts for tag in prompt.get("tags", [])}
        self.assertIn("create", tags)
        self.assertIn("edit", tags)
        self.assertIn("existing-video", tags)
        self.assertIn("destructive-without-authorization", tags)
        self.assertIn("compression-without-authorization", tags)
        self.assertIn("executed", tags)

    def test_evals_include_unauthorized_destructive_case(self):
        payload = json.loads(EVALS_PATH.read_text(encoding="utf-8"))
        destructive = [
            prompt
            for prompt in payload["prompts"]
            if "destructive-without-authorization" in prompt.get("tags", [])
        ]
        self.assertEqual(len(destructive), 1)
        self.assertIn("删除", destructive[0]["prompt"])
        self.assertIn("拒绝", destructive[0]["expected"])

    def test_evals_include_unauthorized_compression_case(self):
        payload = json.loads(EVALS_PATH.read_text(encoding="utf-8"))
        compression = [
            prompt
            for prompt in payload["prompts"]
            if "compression-without-authorization" in prompt.get("tags", [])
        ]
        self.assertEqual(len(compression), 1)
        self.assertIn("压缩", compression[0]["prompt"])
        self.assertIn("显式授权", compression[0]["expected"])
        self.assertIn("不得声称已有最终 MP4", compression[0]["expected"])
        self.assertIn("拟定输出路径", compression[0]["expected"])
        self.assertIn("build 结果必须缺席", compression[0]["expected"])
        self.assertIn("verify 结果必须缺席", compression[0]["expected"])
        self.assertIn("visual 结果必须缺席", compression[0]["expected"])
        self.assertIn("safety 结果必须缺席", compression[0]["expected"])

    def test_skill_planned_output_omits_completed_execution_results(self):
        content = SKILL_PATH.read_text(encoding="utf-8")
        self.assertIn("planned", content)
        self.assertIn("build/verify/visual/safety 结果", content)
        self.assertIn("不得声称已有", content)
        self.assertIn("缺席", content)

    def test_evals_include_executed_positive_case(self):
        payload = json.loads(EVALS_PATH.read_text(encoding="utf-8"))
        executed = [
            prompt
            for prompt in payload["prompts"]
            if "executed" in prompt.get("tags", [])
        ]
        self.assertEqual(len(executed), 1)
        expected = executed[0]["expected"]
        self.assertIn("最终 MP4", expected)
        self.assertIn("字幕", expected)
        self.assertIn("时间轴", expected)
        self.assertIn("验收报告", expected)
        self.assertIn("build", expected)
        self.assertIn("verify", expected)
        self.assertIn("visual", expected)
        self.assertIn("safety", expected)

    def test_skill_output_contract_requires_delivery_paths(self):
        content = SKILL_PATH.read_text(encoding="utf-8")
        self.assertIn("最终 MP4", content)
        self.assertIn("绝对路径", content)
        self.assertIn("仓库内安全路径", content)
        self.assertIn("字幕", content)
        self.assertIn("时间轴", content)
        self.assertIn("验收报告", content)

    def test_skill_output_contract_distinguishes_planned_and_executed(self):
        content = SKILL_PATH.read_text(encoding="utf-8")
        self.assertIn("planned", content)
        self.assertIn("executed", content)
        self.assertIn("未授权", content)
        self.assertIn("风险说明", content)
        self.assertIn("待确认操作", content)
        self.assertIn("拟定输出路径", content)
        self.assertIn("只有授权并实际构建后", content)

    def test_skill_unauthorized_path_does_not_claim_final_artifacts(self):
        content = SKILL_PATH.read_text(encoding="utf-8")
        self.assertIn("不得声称已有最终 MP4", content)
        self.assertIn("不得声称已有最终 MP4/字幕/时间轴/验收报告", content)

    def test_readme_mentions_mp4_output_path_requirement(self):
        lines = README_PATH.read_text(encoding="utf-8").splitlines()
        self.assertTrue(
            any("最终 MP4" in line and "绝对路径或仓库内安全路径" in line for line in lines)
        )

    def test_readme_mentions_subtitle_output_path_requirement(self):
        lines = README_PATH.read_text(encoding="utf-8").splitlines()
        self.assertTrue(
            any("字幕" in line and "绝对路径或仓库内安全路径" in line for line in lines)
        )

    def test_readme_mentions_timeline_output_path_requirement(self):
        lines = README_PATH.read_text(encoding="utf-8").splitlines()
        self.assertTrue(
            any("时间轴" in line and "绝对路径或仓库内安全路径" in line for line in lines)
        )

    def test_readme_mentions_report_output_path_requirement(self):
        lines = README_PATH.read_text(encoding="utf-8").splitlines()
        self.assertTrue(
            any("验收报告" in line and "绝对路径或仓库内安全路径" in line for line in lines)
        )


if __name__ == "__main__":
    unittest.main()
