"""数据目录解析（原 store.py 内联逻辑，抽出）。

与存储后端无关：AGENT_DATA_DIR 隔离（测试/部署）、默认 data/selection
（fixture 数据集所在，缺失即报错——P20 教训，不静默重建）。
"""
from __future__ import annotations

import os
from pathlib import Path


def resolve_data_dir() -> Path:
    """return the resolved data dir. Behavior is environment-dependent:
    * ``AGENT_DATA_DIR`` explicitly set → the directory must be creatable
      (callers usually pass a temp dir for tests and own the lifecycle).
      If the parent is not writable we surface a loud error so the
      misconfiguration is not silently swallowed.
    * default path (no env) → we DO NOT silently create it. Missing
      ``data/selection`` directory is a real failure (the bundled fixture
      data set should always be there on a clean clone); this used to be
      the masking point behind P20 — a missing dir got rebuilt, hiding the
           underlying problem from the operator.

    Returns the Path; raises RuntimeError with a remediation hint on the
    two failure modes.
    """
    explicit = os.environ.get("AGENT_DATA_DIR")
    if explicit:
        path = Path(explicit)
        parent = path.parent if path.parent != Path(".") else Path.cwd()
        if parent.exists() and not os.access(parent, os.W_OK):
            raise RuntimeError(
                f"AGENT_DATA_DIR={path} parent ({parent}) is not writable; "
                f"check directory permissions or unset AGENT_DATA_DIR to use the default.",
            )
        return path
    default = Path(__file__).resolve().parents[3] / "data" / "selection"
    if not default.exists():
        raise RuntimeError(
            f"data/selection 缺失（含 fixture 数据集，检查是否完整克隆）",
        )
    return default
