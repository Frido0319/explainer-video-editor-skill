# Existing-video edit runtime

The public package supports `mode=edit` through the same CLI contract as create:

- `python3 -m explainer_video_editor.cli validate examples/edit_minimal/project.json`
- `python3 -m explainer_video_editor.cli build examples/edit_minimal/project.json`
- `python3 -m explainer_video_editor.cli verify examples/edit_minimal/project.json`

## Timeline model

An edit project declares one or more source records and an explicit operation list. Source records use either:

- `path`: a local media file supplied by the user, resolved relative to the project file.
- `generator: "synthetic"`: a short deterministic test clip generated during build. This is intended for examples and tests so no media file is committed.

Supported operations:

- `keep`: explicitly retains only the declared source ranges for that source.
- `cut`: removes a source range. Requires `"authorized": true`.
- `compress`: speeds up a source range with `factor > 1.0`. Requires `"authorized": true`.
- `reorder`: emits declared retained ranges in the declared order, then preserves any remaining retained ranges automatically. Requires `"authorized": true`.
- `zoom`: applies a controlled zoom over a retained range. The public cap is `1.08`.
- `callout`: draws a temporary label box and keeps it above the subtitle safe band.
- `subtitle_rebase`: reads ASS dialogue events and rebases retained events onto the compiled output timeline.

When no `keep` operation is present, unlisted source ranges are preserved automatically. For example, cutting `1.0-1.5` and compressing `2.0-3.0` on a three-second clip produces:

1. keep `0.0-1.0`
2. keep `1.5-2.0`
3. compress `2.0-3.0`

`cut` and `compress` are applied before `reorder`, so a reorder request cannot reintroduce removed ranges or bypass compression.

## Authorization boundary

The loader rejects unapproved destructive operations before build:

- `cut` without `"authorized": true`
- `compress` without `"authorized": true`
- `reorder` without `"authorized": true`

In an unapproved workflow, return the selected mode, resolved inputs, risk note, operations waiting for confirmation, and planned output path only. Do not claim final MP4, subtitles, compiled timeline, verification report, build, verify, visual review, or safety results until the user authorizes and a real build/verify run has completed.

## Build outputs

`build` writes the final MP4 to `output_dir/output_name` and audit files under `output_dir/work/`:

- `edit_timeline.compiled.json`
- `rebased.ass` when subtitle rebasing is declared, or an empty generated ASS header when no subtitle events are present
- generated synthetic sources and intermediate segment files for example/test projects

`verify` checks output existence, duration tolerance, H.264 video, declared resolution, and AAC 44.1 kHz stereo audio.

For release verification, also run the deterministic offline media checks:

- `python3 scripts/render_test_media.py /tmp/explainer-media-fixture`
- `python3 scripts/verify_media.py /tmp/explainer-media-fixture/deterministic_media.mp4 --expected-duration 3 --expected-width 1920 --expected-height 1080 --expected-fps 24 --subtitle-ass /tmp/explainer-media-fixture/subtitles.ass --video-without-subtitles /tmp/explainer-media-fixture/video_without_subtitles.mp4 --voice-stem /tmp/explainer-media-fixture/voice_stem.wav --bgm-stem /tmp/explainer-media-fixture/bgm_stem.wav --subtitle-safe-y 820`

The verifier uses local synthetic media only. It validates ffprobe metadata, playable duration, required dimensions, subtitle safe-band placement, audio peak, BGM/voice layer balance, loudness-ramp regression, and black-frame regression. Generated MP4/WAV/PNG files are local test artifacts and must not be committed.
