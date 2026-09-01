# Minimal Public Create Example

This repository-safe example demonstrates `create` mode using only neutral card content and synthetic audio that needs no external credentials:

```bash
python3 -m explainer_video_editor.cli validate examples/create_minimal/project.json
python3 -m explainer_video_editor.cli build examples/create_minimal/project.json
python3 -m explainer_video_editor.cli verify examples/create_minimal/project.json
```

Generated files are written to `examples/create_minimal/output/` and are ignored by Git.
