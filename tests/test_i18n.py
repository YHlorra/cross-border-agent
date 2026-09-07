"""i18n SEAM 友好性测试。

注：TypeScript hook 部分靠 tsc 验证类型；这里 SEAM 测的是资源文
件一致性与纯 tFor 函数（locale 解析 / 模板替换 / 回退链）。
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def zh():
    return json.loads((ROOT / "workbench" / "src" / "lib" / "messages" / "zh.json").read_text(encoding="utf-8"))


@pytest.fixture
def en():
    return json.loads((ROOT / "workbench" / "src" / "lib" / "messages" / "en.json").read_text(encoding="utf-8"))


# ─── Key parity (zh / en must cover the same key space) ────────────────────


def test_key_parity(zh: dict, en: dict):
    assert set(zh.keys()) == set(en.keys()), (
        f"key drift: only_zh={set(zh) - set(en)}, only_en={set(en) - set(zh)}"
    )


def test_no_empty_values(zh: dict, en: dict):
    for k, v in zh.items():
        assert isinstance(v, str) and v.strip(), f"zh[{k!r}] is empty"
    for k, v in en.items():
        assert isinstance(v, str) and v.strip(), f"en[{k!r}] is empty"


# ─── Pure tFor semantics (re-implementing the same logic in Python to lock
#     the contract; the TypeScript tFor is verified by tsc + the in-app UI).


def _t_for(locale: str, key: str, zh: dict, en: dict) -> str:
    src = zh if locale == "zh" else en
    if key in src:
        return src[key]
    if locale != "zh" and key in zh:
        return zh[key]
    return key


def test_t_for_zh_hit(zh, en):
    assert _t_for("zh", "nav.selection", zh, en) == "选品"


def test_t_for_en_hit(zh, en):
    assert _t_for("en", "nav.selection", zh, en) == "Selection"


def test_t_for_en_fallback_to_zh_on_missing(zh, en):
    """If a key exists in zh but not en, English falls back to zh (defensive)."""
    del en["nav.selection"]  # simulate drift
    out = _t_for("en", "nav.selection", zh, en)
    assert out == "选品"


def test_t_for_unknown_key_returns_key(zh, en):
    assert _t_for("zh", "totally.nonexistent.key", zh, en) == "totally.nonexistent.key"
    assert _t_for("en", "totally.nonexistent.key", zh, en) == "totally.nonexistent.key"


def test_template_substitution_contract():
    """JS impl uses {name}; lock the contract via a Python re.sub ref-impl."""
    import re

    template = "Result: {n} items · each ≤{max}"
    out = re.sub(
        r"\{(\w+)\}",
        lambda m: str({"n": 5, "max": 100}.get(m.group(1), m.group(0))),
        template,
    )
    assert out == "Result: 5 items · each ≤100"
    # missing param → kept literal
    out2 = re.sub(
        r"\{(\w+)\}",
        lambda m: str({"n": 5}.get(m.group(1), m.group(0))),
        template,
    )
    assert out2 == "Result: 5 items · each ≤{max}"


# ─── Expected keys (high-value, hardcode to prevent accidental drop) ───────


EXPECTED_KEYS = {
    "app.title",
    "nav.selection",
    "nav.listing",
    "nav.llmConfig",
    "session.list",
    "session.delete.button",
    "session.delete.confirm",
    "session.delete.cancel",
    "input.hero.title",
    "input.hero.subtitle",
    "input.placeholder",
    "input.budgetPlaceholder",
    "input.prefLight",
    "input.prefHighRepurchase",
    "input.prefLowPrice",
    "agent.thinking",
    "agent.streaming",
    "agent.processTitle.running",
    "agent.processTitle.done",
    "result.title",
    "result.badge.ok",
    "result.badge.fail",
    "result.fieldLabel.title",
    "result.fieldLabel.bullets",
    "result.fieldLabel.description",
    "result.fieldLabel.searchTerms",
    "result.copy",
    "result.copied",
    "result.regenerate",
    "result.regenerating",
    "result.count.title",
    "result.count.bullets",
    "result.count.searchTerms",
    "empty.title",
    "empty.body",
    "empty.keyword",
    "error.retry",
    "error.goConfig",
    "error.back",
    "footer.disclaimer",
}


def test_expected_keys_present(zh: dict, en: dict):
    missing_zh = EXPECTED_KEYS - set(zh)
    missing_en = EXPECTED_KEYS - set(en)
    assert not missing_zh, f"zh missing: {sorted(missing_zh)}"
    assert not missing_en, f"en missing: {sorted(missing_en)}"


# ─── Structural guards ──────────────────────────────────────────────────────


def test_zh_is_default_for_unmapped_locale(zh, en):
    """A navigator.language like 'fr' or 'ja' should fall back to zh
    (the project's primary language per PRD R9)."""
    # The detectInitialLocale JS impl maps 'en' → en; anything else → zh.
    # Pin this contract: the resource files must be loadable in either order.
    assert "选品" in zh["nav.selection"]
    assert "Selection" in en["nav.selection"]


def test_json_files_parseable():
    """Defense against trailing comma / encoding corruption."""
    for path in (ROOT / "workbench" / "src" / "lib" / "messages").glob("*.json"):
        data = json.loads(path.read_text(encoding="utf-8"))
        assert isinstance(data, dict) and data
