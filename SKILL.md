---
name: explainer-video-editor-skill
description: Route natural-language video requests into either create mode for building an explainer video from source materials or edit mode for modifying an existing video, then require validate/build/verify CLI steps plus visual inspection and public-release safety checks.
---

# explainer-video-editor-skill

## Purpose

This skill is the public entrypoint for two workflows only:

- `mode=create`: 从素材制作讲解视频
- `mode=edit`: 已有视频编辑

Do not present version labels or branch-selection language to the user. Route directly from the request intent and supplied paths.

## Trigger

Use this skill when the user wants to:

- 从 PPT、脚本、图片、录屏片段或其他素材制作讲解视频
- 对一个已存在的视频做字幕、配音、BGM、裁剪、增强、压缩、重排或局部修订
- 在最终交付前要求统一经过命令行校验、构建和验收

## Inputs

Collect or confirm:

- 用户目标
- 输入素材路径
- 输出目录或目标文件名
- 是否允许结构性改动

## Routing

Choose exactly one mode:

1. `mode=create`
   - 当用户要从素材制作一个新视频
   - 常见输入：PPT、讲稿、图片、表格、原始素材目录

2. `mode=edit`
   - 当用户已经有一个现成视频，希望在其基础上修改
   - 常见输入：现有 `.mp4/.mov` 文件及修改说明

If the request is ambiguous, ask the smallest clarifying question needed to decide between `mode=create` and `mode=edit`.

## Authorization boundary

- 保守编辑默认允许：字幕、配音、BGM、音量、封面、轻量转场、标注、高亮、节奏微调
- 涉及“删除”现有片段、做有损“压缩”、重写主要叙事、或对时间线做大幅“重排”时，必须取得用户显式授权
- 如果用户没有明确授权删除、压缩或重排，只能提出方案，不能直接执行 destructive edit
- 对来源不明、可能受限的素材，先提示版权与公开发布风险

## Required CLI flow

For both modes, run these commands in order:

1. `python3 -m explainer_video_editor.cli validate`
2. `python3 -m explainer_video_editor.cli build`
3. `python3 -m explainer_video_editor.cli verify`

Explain the selected mode, resolved inputs, and planned output before build if any path or authorization is unclear.

## Verification

Required acceptance checks:

- 命令行校验通过
- 输出文件存在且可播放
- 关键时刻做视觉验收，确认字幕、布局、裁剪、空白边、遮挡和时序
- 如有音频修改，检查音画同步、音量层次和尾段收束

Do not claim completion from logs alone.

## Public release safety gate

这是公开发布前必须执行的安全扫描。

Before a public release or commit intended for publication, run:

- `python3 scripts/scan_public_release.py .`

The release must not expose:

- 私有路径
- 密钥、token、cookie、证书
- 内部控制文档或工作记录

## Output contract

Distinguish `planned` from `executed`.

### planned

未授权删除、压缩或重排时，只能返回：

- selected mode (`mode=create` or `mode=edit`)
- resolved inputs
- 风险说明
- 待确认操作
- 拟定输出路径

在这个阶段，不得声称已有最终 MP4/字幕/时间轴/验收报告；build/verify/visual/safety 结果必须缺席，不得声称已有这些已完成结果。

### executed

只有授权并实际构建后，才返回：

- selected mode (`mode=create` or `mode=edit`)
- resolved inputs
- whether destructive operations were authorized
- 最终 MP4 产物路径（绝对路径或仓库内安全路径）
- 字幕路径（绝对路径或仓库内安全路径）
- 时间轴路径（绝对路径或仓库内安全路径）
- 验收报告路径（绝对路径或仓库内安全路径）
- build result
- verify result
- visual inspection result
- safety scan result
