# explainer-video-editor-skill

这是公开版 Skill 入口，不暴露版本分流入口，也不依赖旧版私有实现。

它只支持两条模式：

- `create`：从素材制作讲解视频
- `edit`：对已有视频编辑

路由规则：

- 用户给出 PPT、脚本、图片、素材目录，要生成一个新视频时，走 `create`
- 用户给出已存在的视频文件，要加字幕、配音、BGM、裁剪、压缩或局部增强时，走 `edit`

授权边界：

- 常规增强可直接执行
- 涉及删除现有片段、有损压缩或大幅重排时间线时，必须得到用户显式授权
- 未获授权时，只能说明风险和方案，不能直接做 destructive edit

统一 CLI 入口：

- `python3 -m explainer_video_editor.cli validate`
- `python3 -m explainer_video_editor.cli build`
- `python3 -m explainer_video_editor.cli verify`

验收要求：

- 输出文件存在且可播放
- 用 `ffprobe` 核对容器时长、H.264 视频、AAC 44.1 kHz stereo 音频和输出尺寸
- `create` 输出必须是 1920x1080；`edit` 输出必须匹配项目清单声明尺寸
- 对关键画面做抽帧视觉验收，并检查字幕只落在安全带内
- 音频改动要检查可听峰值、BGM 低于 voice 层、无异常响度爬坡和收尾
- 用黑帧检测防止导出黑屏回归
- 公开发布前运行安全扫描

输出合同：

- 未授权删除、有损压缩或重排时，只能返回 selected mode、resolved inputs、风险说明、待确认操作、拟定输出路径
- 未授权阶段不得声称已有最终 MP4、字幕、时间轴、验收报告，也不得声称已有 build/verify/visual/safety 结果
- 实际构建完成后，必须逐项返回最终 MP4 的绝对路径或仓库内安全路径
- 实际构建完成后，必须逐项返回字幕的绝对路径或仓库内安全路径
- 实际构建完成后，必须逐项返回时间轴的绝对路径或仓库内安全路径
- 实际构建完成后，必须逐项返回验收报告的绝对路径或仓库内安全路径

公开发布前，先运行：

- `python3 -m unittest tests.test_public_release tests.test_skill_contract -v`
- `python3 -m unittest tests.test_media_verification -v`
- `python3 scripts/scan_public_release.py .`

当前仓库内实际可直接运行的 Python runtime 覆盖 `create` 和 `edit` 项目清单，包接口如下：

- `manifest.load_project(path)`
- `builder.build(project)`
- `verify.verify_project(project)`
- `timeline.load_timeline(path)`
- `editing.compile_edit_timeline(timeline)`
- `editing.build_edit(project)`

`edit` runtime 已集成到同一个包：

- 支持 `keep`、`cut`、`compress`、`reorder`、`zoom`、`callout`、`subtitle_rebase`
- `zoom` 上限为 `1.08`
- `cut`、`compress`、`reorder` 必须显式授权，否则 validate 阶段拒绝
- `keep` 存在时，仅保留显式声明的 source range
- `reorder` 只改变显式片段顺序；未列出的已保留 source range 仍会自动保留
- `cut` / `compress` 会先作用于 source range，不能被 `reorder` 绕过
- 会写出可审计的 compiled timeline
- 示例位于 `examples/edit_minimal/project.json`，使用合成短片，不提交媒体

CLI 依然统一为：

- `python3 -m explainer_video_editor.cli validate`
- `python3 -m explainer_video_editor.cli build`
- `python3 -m explainer_video_editor.cli verify`

最小公开示例位于 `examples/create_minimal/project.json`：

- 只使用合成卡片
- 默认走 `audio.generator=synthetic`，不需要外部凭据
- 构建与验收产物写入示例目录下的 `output/`

推荐本地验收顺序：

- `python3 -m unittest tests.test_manifest tests.test_create_pipeline -v`
- `python3 -m unittest tests.test_timeline tests.test_editing -v`
- `python3 -m unittest tests.test_media_verification -v`
- `python3 -m explainer_video_editor.cli validate examples/create_minimal/project.json`
- `python3 -m explainer_video_editor.cli build examples/create_minimal/project.json`
- `python3 -m explainer_video_editor.cli verify examples/create_minimal/project.json`
- `python3 -m explainer_video_editor.cli validate examples/edit_minimal/project.json`
- `python3 -m explainer_video_editor.cli build examples/edit_minimal/project.json`
- `python3 -m explainer_video_editor.cli verify examples/edit_minimal/project.json`
- `python3 scripts/scan_public_release.py .`

离线媒体验收可先生成确定性合成素材，再运行独立 verifier：

- `python3 scripts/render_test_media.py /tmp/explainer-media-fixture`
- `python3 scripts/verify_media.py /tmp/explainer-media-fixture/deterministic_media.mp4 --expected-duration 3 --expected-width 1920 --expected-height 1080 --expected-fps 24 --subtitle-ass /tmp/explainer-media-fixture/subtitles.ass --video-without-subtitles /tmp/explainer-media-fixture/video_without_subtitles.mp4 --voice-stem /tmp/explainer-media-fixture/voice_stem.wav --bgm-stem /tmp/explainer-media-fixture/bgm_stem.wav --subtitle-safe-y 820`

这些素材只用于本地验收，不访问网络、不读取凭据，也不要提交生成的 MP4、WAV、PNG 或本机绝对路径。
