"""SP-API credential storage — mirrors providers_store.

AGENT_DATA_DIR 旋钮同理：E2E/测试用隔离目录，全程不碰真实凭据。
key 不入库（.gitignore: data/selection/spapi.json）。
"""
from __future__ import annotations

import json
import os
import threading
from pathlib import Path

_DATA_DIR = Path(
    os.environ.get("AGENT_DATA_DIR")
    or (Path(__file__).resolve().parents[3] / "data" / "selection")
)
_FILE = _DATA_DIR / "spapi.json"
_lock = threading.Lock()

_FIELDS = (
    "client_id",
    "client_secret",
    "refresh_token",
    "seller_id",
    "marketplace_ids",
    "region",
)
_SECRET_FIELDS = ("client_secret", "refresh_token")


def _read() -> dict:
    try:
        data = json.loads(_FILE.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, json.JSONDecodeError):
        return {}


def _write(creds: dict) -> None:
    _FILE.parent.mkdir(parents=True, exist_ok=True)
    _FILE.write_text(
        json.dumps(creds, ensure_ascii=False, indent=2), encoding="utf-8"
    )


def _mask(value: str) -> str:
    return f"••••{value[-4:]}" if len(value) > 4 else "••••"


def _public(creds: dict) -> dict:
    """掩码视图：密钥只留尾 4 位（照 providers_store._public）。"""
    out: dict = {}
    for k, v in creds.items():
        if k in _SECRET_FIELDS and isinstance(v, str):
            out[k] = _mask(v)
        else:
            out[k] = v
    return out


def load_spapi_creds() -> dict:
    """缺失/损坏返回 {}（与 providers_store._read 同约定），通道检测交给 resolve_upload_channel。"""
    with _lock:
        return _read()


def save_spapi_creds(creds: dict) -> dict:
    """Upsert 凭据（传什么存什么，字段外的不收）；返回掩码视图。"""
    clean: dict = {}
    for k in _FIELDS:
        if k not in creds:
            continue
        v = creds[k]
        if k == "marketplace_ids":
            clean[k] = [str(x).strip() for x in v if str(x).strip()]
        else:
            clean[k] = str(v).strip()
    with _lock:
        current = _read()
        current.update(clean)
        _write(current)
        return _public(current)
