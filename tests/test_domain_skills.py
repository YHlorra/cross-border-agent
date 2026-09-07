"""SEAM — 电商领域 skill。

验证 skill 文件契约（prompts/skills/*.md）、选品/Listing 系统提示词装配、
general agent 的 load_skill 渐进披露工具。无 LLM。
"""
from __future__ import annotations

import json
from pathlib import Path

from agent.prompts import (
    list_skills,
    load_prompt,
    load_selection_system_prompt,
    load_skill,
)
from agent.agents.listing_agent import build_listing_tools

_SKILL_NAMES = (
    "ecom-selection-method",
    "ecom-imagegen",
    "ecom-listing-copywriting",
    "ecom-listing-platform-rules",
    "ecom-review-mining",
)


# ─── skill 文件契约 ──────────────────────────────────────────────────────────


def test_skills_discoverable_with_frontmatter() -> None:
    """三个领域 skill 可被 load_prompt(subdir='skills') 读到，frontmatter 的
    name/description 完整（朴素解析器不支持块标量——description 必须单行）。"""
    skills = {s["name"]: s for s in list_skills()}
    for name in _SKILL_NAMES:
        assert name in skills, f"{name} 未被 list_skills 发现"
        assert skills[name]["description"], f"{name} 缺 description（路由判据）"
        meta, body = load_prompt(name, subdir="skills")
        assert meta.get("name") == name
        # 正文含实质内容（非空且长度合理，防手滑存成空壳）
        assert len(body) > 300, f"{name} 正文过短（{len(body)} 字符）"


def test_selection_method_carries_language_contract() -> None:
    """选品方法论 skill 必须包含平台-语言契约（selection-skill 的核心规则，
    从 agent.md 抽出的内容不能丢）。"""
    body = load_skill("ecom-selection-method")
    assert body is not None
    assert "英文" in body and "中文" in body
    assert "corpus_overview" in body and "search_1688" in body


def test_imagegen_carries_six_elements() -> None:
    body = load_skill("ecom-imagegen")
    assert body is not None
    assert "六要素" in body and "合规" in body and "画幅" in body


def test_listing_copywriting_carries_keyword_layers() -> None:
    body = load_skill("ecom-listing-copywriting")
    assert body is not None
    assert "四级" in body and "五点" in body and "覆盖率" in body


def test_listing_copywriting_v11_fact_lock_and_localization() -> None:
    """v1.1 增补：事实锁定 / 本地化≠翻译 / 三风格变体三节必须在场。"""
    body = load_skill("ecom-listing-copywriting")
    assert body is not None
    assert "事实锁定" in body and "leave it out" in body
    assert "本地化" in body and "德语" in body and "日语" in body
    assert "变体" in body and "亚马逊例外" in body


def test_platform_rules_carries_numbers_and_code_mapping() -> None:
    """平台规则 skill 必须含三平台数字 + 与 marketplaces.json/校验器的硬编码
    映射（平台规则是代码约束不是 prompt 软约束——设计原则的锚点）。"""
    body = load_skill("ecom-listing-platform-rules")
    assert body is not None
    assert "75" in body and "TikTok" in body and "Shopify" in body
    assert "marketplaces.json" in body and "forbidden_phrases" in body
    assert "hard_fail" in body
    assert "德文" in body  # 站点语言匹配表


def test_review_mining_carries_thresholds_and_honesty() -> None:
    """差评→卖点反向链路：A/B 对照、信号阈值、需求信号词库、不编造纪律。"""
    body = load_skill("ecom-review-mining")
    assert body is not None
    assert "A 类" in body and "B 类" in body
    assert "10%" in body and "60%" in body
    assert "wish it had" in body
    assert "不编造" in body


# ─── slash 解耦契约（ Addendum 5） ─────────────────────────────


def test_slash_tokens_round_trip_frontend_to_backend() -> None:
    """commands.ts 里的每条 chat 命令的 token 都能被后端 slash_skill_appendix
    路由；任何漂移（后端删了 / 加了没同步前端）在这里爆。

    跨层读取：纯文件读取 + 后端纯函数——不依赖两套配置,改一边都会立刻
    被另一边对齐出来。
    """
    import re
    from agent.prompts import slash_skill_appendix

    cmds_path = Path(__file__).resolve().parents[1] / "workbench/src/lib/commands.ts"
    src = cmds_path.read_text(encoding="utf-8")
    # 拆 COMMANDS 数组：每个 entry 块 = `{\n    id: ..., ...\n  }`
    entry_blocks = re.findall(r"\{\s*\n([^}]*?)\n\s*\}", src)
    chat_tokens: set[str] = set()
    for block in entry_blocks:
        if 'kind: "chat"' not in block and "kind: 'chat'" not in block:
            continue
        # label + id + aliases 全部视为可被 / 唤起的 token
        for m in re.finditer(r'(?:label|id):\s*"([^"]+)"', block):
            chat_tokens.add(m.group(1).lower())
        m_aliases = re.search(r"aliases:\s*\[([^\]]*)\]", block)
        if m_aliases:
            for a in re.findall(r'"([^"]+)"', m_aliases.group(1)):
                chat_tokens.add(a.lower())
    assert len(chat_tokens) >= 5, f"chat token 集合过小（{len(chat_tokens)}），命令解析正则可能失效"

    missing = sorted(t for t in chat_tokens if slash_skill_appendix(f"/{t} 随便") is None)
    assert not missing, f"commands.ts chat tokens 中这些未被后端路由：{missing}"


def test_slash_skill_routes_cover_all_frontmatter_slash_aliases() -> None:
    """反向：每个 skill frontmatter 声明的 slash token 也都能被路由；并
    验证派生路由的别名集合（一个 skill 的多 alias 全部可路由）。"""
    from agent.prompts import _skill_slash_routes, list_skills

    for entry in list_skills():
        aliases = [a.strip().lower() for a in str(entry.get("slash") or "").split(",") if a.strip()]
        if not aliases:
            continue
        routes = _skill_slash_routes()
        for alias in aliases:
            assert routes.get(alias) == entry["name"], (
                f"alias {alias!r} 路由到 {routes.get(alias)!r}，期望 {entry['name']!r}"
            )


# ─── 选品系统提示词装配 ──────────────────────────────────────────────────────


def test_selection_system_prompt_appends_method_skill() -> None:
    """agent.md（角色/契约）+ ecom-selection-method（打法附录）拼装——
    主路径 build_selection_agent 与 tokens 变体共用本函数。"""
    prompt = load_selection_system_prompt()
    assert "选品智能体" in prompt  # agent.md 角色
    assert "# 附录：选品方法论" in prompt  # skill 注入标记
    assert "平台与语言" in prompt  # skill 内容
    # 方法论从 agent.md 抽出后，agent.md 自身不再重复平台-语言表（去重契约）
    _, agent_body = load_prompt("agent")
    assert "平台与语言" not in agent_body


# ─── listing agent 条件注入 ─────────────────────────────────────────────────


def test_listing_prompt_injects_imagegen_only_with_image_tool(monkeypatch) -> None:
    """HUIWA 键存在（生图工具注册）→ 系统提示词含生图 SOP；无键 → 不含。"""
    from agent.agents.listing_agent import compose_listing_system_prompt

    monkeypatch.delenv("HUIWA_API_KEY", raising=False)
    tools_no_key = build_listing_tools()
    prompt_no_key = compose_listing_system_prompt(tools_no_key)
    assert "商品生图 SOP" not in prompt_no_key

    monkeypatch.setenv("HUIWA_API_KEY", "test-key")
    prompt_with_key = compose_listing_system_prompt(build_listing_tools())
    assert "商品生图 SOP" in prompt_with_key
    assert "六要素" in prompt_with_key


# ─── load_skill 工具（渐进披露 L1/L2） ───────────────────────────────────────


def _skill_tool():
    from agent.agents.tools import build_skill_tools

    return build_skill_tools()[0]


def test_load_skill_directory_mode_lists_all() -> None:
    out = json.loads(_skill_tool().invoke({"name": ""}))
    names = {s["name"] for s in out["skills"]}
    assert set(_SKILL_NAMES) <= names


def test_load_skill_known_name_returns_body() -> None:
    raw = _skill_tool().invoke({"name": "ecom-imagegen"})
    assert "六要素" in raw  # 全文，不是 JSON 包络


def test_load_skill_unknown_name_structured_error() -> None:
    out = json.loads(_skill_tool().invoke({"name": "no-such-skill"}))
    assert "error" in out
    assert "ecom-imagegen" in out["available"]


# ─── 平台规则硬编码：校验器强制 claim 禁令 ───────────────────────────────────


def test_validator_enforces_claim_ban_phrases() -> None:
    """marketplaces.json 的 claim 禁令（价格折扣/库存紧迫/物流承诺）由校验器
    hard_fail 强制——平台规则 skill 的禁令表有代码行为支撑，非 prompt 软约束。"""
    from agent.listing.schema import ListingOutput, load_market_config
    from agent.listing.validate import validate_listing

    market = load_market_config()
    draft = ListingOutput(
        item_name="Air Fryer 50% off Best Price Limited Stock",
        bullet_point=["Cooks fast and crispy every day with ease"] * 5,
        product_description="Great fryer on sale now.",
        generic_keyword="air fryer",
    )
    result = validate_listing(draft, market)
    assert result.hard_failed
    codes = {i.code for i in result.issues}
    assert "title_forbidden_phrase" in codes
    assert "description_forbidden_phrase" in codes
