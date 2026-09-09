from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

PLUGIN_DIR = Path(__file__).resolve().parent.parent


def load_module(module_name: str, file_path: Path):
    spec = importlib.util.spec_from_file_location(module_name, file_path)
    assert spec and spec.loader, f'cannot load spec for {module_name}'
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)  # type: ignore[attr-defined]
    return module


def test_python_sources_are_importable() -> None:
    sys.path.insert(0, str(PLUGIN_DIR))
    try:
        expectations = [{'path': 'provider/whisper_pro.py', 'module_name': 'whisper_pro_provider', 'class_name': 'WhisperProProvider'}, {'path': 'tools/transcribe.py', 'module_name': 'tools_transcribe', 'class_name': 'TranscribeTool'}]
        for expectation in expectations:
            module = load_module(expectation['module_name'], PLUGIN_DIR / expectation['path'])
            assert hasattr(module, expectation['class_name'])
    finally:
        sys.path.remove(str(PLUGIN_DIR))
