#!/usr/bin/env python3
"""Exercise the pure-Python runtime dependency installer without network access."""

from __future__ import annotations

import hashlib
import importlib
import importlib.util
import io
import json
import sys
import tempfile
import zipfile
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
MODULE_PATH = ROOT / "TMessagesProj/src/main/python/_plugin_requirements.py"


def make_wheel(name: str, version: str, module: str, requirements=()) -> bytes:
    normalized = name.replace("-", "_")
    dist_info = f"{normalized}-{version}.dist-info"
    metadata = [
        "Metadata-Version: 2.1",
        f"Name: {name}",
        f"Version: {version}",
    ]
    metadata.extend(f"Requires-Dist: {requirement}" for requirement in requirements)
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w", zipfile.ZIP_DEFLATED) as wheel:
        wheel.writestr(f"{module}/__init__.py", f"VALUE = {version!r}\n")
        wheel.writestr(f"{dist_info}/METADATA", "\n".join(metadata) + "\n\n")
        wheel.writestr(f"{dist_info}/WHEEL", "Wheel-Version: 1.0\nTag: py3-none-any\n")
    return output.getvalue()


def load_module():
    spec = importlib.util.spec_from_file_location("requirements_under_test", MODULE_PATH)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Cannot import {MODULE_PATH.relative_to(ROOT)}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def main() -> int:
    module = load_module()
    errors: list[str] = []

    if not module._is_universal_wheel("example-1.0-py3-none-any.whl"):
        errors.append("a compatible universal Python 3 wheel was rejected")
    if module._is_universal_wheel("example-1.0-py2-none-any.whl"):
        errors.append("an incompatible Python 2-only wheel was accepted")

    child = make_wheel("zasto-test-child", "2.1.0", "zasto_test_child")
    parent = make_wheel(
        "zasto-test-parent", "1.0.0", "zasto_test_parent",
        requirements=("zasto-test-child>=2",),
    )
    wheels = {
        "https://example.invalid/child.whl": child,
        "https://example.invalid/parent.whl": parent,
    }
    projects = {
        "zasto-test-child": ("2.1.0", "child.whl", child),
        "zasto-test-parent": ("1.0.0", "parent.whl", parent),
    }

    def fake_json(url: str):
        name = url.removesuffix("/json").rsplit("/", 1)[-1].lower()
        version, filename, content = projects[name]
        return {
            "releases": {
                version: [{
                    "filename": f"{name.replace('-', '_')}-{version}-py3-none-any.whl",
                    "packagetype": "bdist_wheel",
                    "url": f"https://example.invalid/{filename}",
                    "digests": {"sha256": hashlib.sha256(content).hexdigest()},
                    "yanked": False,
                }]
            }
        }

    module._fetch_json = fake_json
    module._fetch_bytes = wheels.__getitem__

    original_path = list(sys.path)
    try:
        with tempfile.TemporaryDirectory() as temporary:
            temporary_path = Path(temporary)
            plugin = temporary_path / "sample.plugin"
            plugin.write_text(
                "__requirements__ = 'zasto-test-parent==1.0.0'\n",
                encoding="utf-8",
            )
            module.configure(temporary_path / "libs")
            if module.requirements_from_source(plugin) != ["zasto-test-parent==1.0.0"]:
                errors.append("legacy string __requirements__ metadata was not normalized")
            module.ensure_requirements(plugin)
            imported_parent = importlib.import_module("zasto_test_parent")
            imported_child = importlib.import_module("zasto_test_child")
            if imported_parent.VALUE != "1.0.0" or imported_child.VALUE != "2.1.0":
                errors.append("resolved parent and transitive dependency were not importable")

            manifest = json.loads((temporary_path / "libs/active.json").read_text(encoding="utf-8"))
            if set(manifest) != {"zasto-test-parent", "zasto-test-child"}:
                errors.append("active dependency manifest does not contain the resolved graph")

            bad = io.BytesIO()
            with zipfile.ZipFile(bad, "w") as wheel:
                wheel.writestr("../escaped.py", "bad = True")
            try:
                module._extract_wheel(bad.getvalue(), temporary_path / "unsafe")
                errors.append("wheel path traversal was accepted")
            except module.RequirementInstallError:
                pass
    finally:
        sys.path[:] = original_path
        sys.modules.pop("zasto_test_parent", None)
        sys.modules.pop("zasto_test_child", None)
        sys.modules.pop("requirements_under_test", None)

    if errors:
        print("Plugin runtime requirements check failed:", file=sys.stderr)
        for error in errors:
            print(f"- {error}", file=sys.stderr)
        return 1
    print("Plugin runtime requirements check passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
