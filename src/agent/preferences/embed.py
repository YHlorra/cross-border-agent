"""偏好嵌入。

两层入口：
- ``real_embed(text) -> list[float]`` — 走 aimux 网关（aimux.create_provider +
  openai_embedding / cohere_embedding / google_embedding）；模型 + 维度由
  model_config.embedding 块决定。
- ``mock_embed(text, dimensions=1536) -> list[float]`` — 确定性 hash → 投影到
  unit vector；SEAM / 单测用，不依赖 aimux。

默认 1536 维（OpenAI text-embedding-3-small）；项目配置可覆盖。
"""
from __future__ import annotations

import hashlib
import math
from typing import Any


DEFAULT_DIMENSIONS = 1536


def mock_embed(text: str, dimensions: int = DEFAULT_DIMENSIONS) -> list[float]:
    """Deterministic hash → L2-normalized vector. Pure, no IO.

    Same text → same vector (cross-session stable). Used by SEAM tests and
    the offline preference build path (where real LLM is unavailable).
    """
    if dimensions <= 0:
        raise ValueError("dimensions must be positive")
    # strict UTF-8 decode so a non-UTF-8 byte in the corpus
    # surfaces as an explicit skip rather than silently becoming a
    # projection of the U+FFFD replacement character (which would corrupt
    # the KNN neighbourhood around legitimate near-duplicates). The
    # common case is one bad document row out of thousands, so the
    # caller treats the zero-vector as "skip me from this round".
    if text:
        try:
            seed = text.encode("utf-8")  # strict mode
        except UnicodeEncodeError:
            import logging
            logging.getLogger(__name__).warning(
                "skip non-utf8 preference text (%d chars)", len(text)
            )
            return [0.0] * dimensions
    else:
        seed = b""
    # SHA-256 → extend to `dimensions` by chaining 32-byte blocks
    out: list[float] = []
    i = 0
    while len(out) < dimensions:
        block = hashlib.sha256(seed + i.to_bytes(4, "big")).digest()
        # each byte → float in [-1, 1]
        for b in block:
            if len(out) >= dimensions:
                break
            out.append((b - 128) / 128.0)
        i += 1
    # L2 normalize
    norm = math.sqrt(sum(x * x for x in out)) or 1.0
    return [x / norm for x in out]


async def real_embed(
    text: str,
    *,
    provider: str = "openai",
    model: str = "text-embedding-3-small",
    api_key: str | None = None,
    base_url: str | None = None,
    dimensions: int = DEFAULT_DIMENSIONS,
    extra_options: dict[str, Any] | None = None,
) -> list[float]:
    """走 aimux 网关生成 embedding（参考  §四）。

    aimux 暴露 ``openai_embedding`` / ``cohere_embedding`` / ``google_embedding``
    三个 provider — 选其一传 provider 即可（其余默认）。
    """
    import aimux  # local import to avoid hard dep on this module import

    if provider == "openai":
        prov = aimux.openai_embedding(api_key=api_key or "", base_url=base_url)
    elif provider == "cohere":
        prov = aimux.cohere_embedding(api_key=api_key or "")
    elif provider == "google":
        prov = aimux.google_embedding(api_key=api_key or "")
    else:
        raise ValueError(f"unsupported embedding provider: {provider}")

    model_obj = prov.model(
        model,
        **(extra_options or {}),
    )
    # aimux EmbeddingModel.embed signature varies slightly per provider;
    # we assume it takes (text: str) and returns an object with .embedding
    # or a list[float]. Wrap with a small adapter.
    res = await model_obj.embed(text)
    if isinstance(res, list):
        return list(res)
    emb = getattr(res, "embedding", None) or getattr(res, "vector", None)
    if emb is None:
        raise RuntimeError(f"aimux embed returned unexpected shape: {res!r}")
    return list(emb)[:dimensions]
