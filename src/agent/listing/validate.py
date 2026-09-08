"""validate_listing — Amazon 格式合规校验层（LLM 草稿的后置防线）。

sanitize_scores 的镜像范式（quality_score.py）：纯函数、返回新对象 + 问题
清单。两类处置：

- **auto_fixed**：机械违规直接修复并留 issue（超长截断、禁用字符/emoji 剥离、
  尾标点剥离、搜索词逗号归一 / 正文去重 / ASIN 与竞品品牌剔除 / 字节钳制）。
- **hard_fail**：内容违规只报告不改写——删促销语或主观断言会改变语义，修正
  走重生成或人工编辑，不静默改写（不诚实的自救比诚实报错
  更糟）。

约束数值全部来自 MarketConfig；字节口径用 UTF-8（保守：≥ Amazon 实际计数，
CJK 场景宁可提前钳——超 250 字节整字段作废，代价不对称）。
"""
from __future__ import annotations

import re

from .schema import (
    ListingIssue,
    ListingOutput,
    MarketConfig,
    MarketFormat,
    ValidationResult,
)

_EMOJI_RE = re.compile(
    "[\U0001F000-\U0001FAFF\U00002600-\U000027BF\U00002B00-\U00002BFF"
    "\U0001F1E6-\U0001F1FF\uFE0F\u200D]+"
)
_ASIN_RE = re.compile(r"^[A-Z0-9]{10}$")
_TRAILING_PUNCT = ".,!?;:。！？；：、…"
_SEARCH_TERM_SEPARATORS = {",": " ", "，": " ", "、": " ", ";": " ", "；": " "}


def _collapse(s: str) -> str:
    return re.sub(r"\s+", " ", s).strip()


def _byte_len(s: str) -> int:
    return len(s.encode("utf-8"))


def _truncate_chars(s: str, limit: int) -> str:
    """词边界截断：先切到 limit，再丢弃半个词（无空格则硬切）。"""
    if len(s) <= limit:
        return s
    cut = s[:limit].rstrip()
    if " " in cut:
        cut = cut[: cut.rfind(" ")].rstrip()
    return cut


def _auto(issues: list[ListingIssue], field: str, code: str, message: str) -> None:
    issues.append(ListingIssue(field=field, code=code, message=message, severity="auto_fixed"))


def _hard(issues: list[ListingIssue], field: str, code: str, message: str) -> None:
    issues.append(ListingIssue(field=field, code=code, message=message, severity="hard_fail"))


def _strip_emoji(s: str, field: str, issues: list[ListingIssue]) -> str:
    cleaned = _collapse(_EMOJI_RE.sub("", s))
    if cleaned != s:
        _auto(issues, field, "emoji_removed", "已移除 emoji / 特殊符号")
    return cleaned


def _fix_title(t: str, fmt: MarketFormat, issues: list[ListingIssue]) -> str:
    t = _strip_emoji(t, "item_name", issues)
    for ch in fmt.title_forbidden_chars:
        if ch in t:
            t = _collapse(t.replace(ch, " "))
            _auto(issues, "item_name", "title_forbidden_char_removed", f"标题移除禁用字符 {ch!r}")
    low = t.lower()
    for phrase in fmt.forbidden_phrases:
        if phrase in low:
            _hard(issues, "item_name", "title_forbidden_phrase", f"标题含促销/违禁语「{phrase}」——需重生成或人工编辑")
    for term in fmt.subjective_terms:
        if re.search(rf"\b{re.escape(term)}\b", low):
            _hard(issues, "item_name", "title_subjective_term", f"标题含主观断言词「{term}」——需重生成或人工编辑")
    if len(t) > fmt.title_max_chars:
        t = _truncate_chars(t, fmt.title_max_chars)
        _auto(issues, "item_name", "title_truncated", f"标题超 {fmt.title_max_chars} 字符（2026-07 新规），已按词边界截断")
    return t


def _fix_bullets(bullets: list[str], fmt: MarketFormat, issues: list[ListingIssue]) -> list[str]:
    out: list[str] = []
    for idx, b in enumerate(bullets, 1):
        b = _strip_emoji(b, "bullet_point", issues)
        trimmed = b.rstrip().rstrip(_TRAILING_PUNCT)
        if trimmed != b:
            _auto(issues, "bullet_point", "bullet_trailing_punctuation_removed", f"第 {idx} 条五点移除尾部标点")
        b = trimmed
        if len(b) > fmt.bullet_max_chars:
            b = _truncate_chars(b, fmt.bullet_max_chars)
            _auto(issues, "bullet_point", "bullet_truncated", f"第 {idx} 条五点超 {fmt.bullet_max_chars} 字符，已截断")
        if 0 < len(b) < fmt.bullet_min_chars:
            _hard(issues, "bullet_point", "bullet_too_short", f"第 {idx} 条五点不足 {fmt.bullet_min_chars} 字符")
        low = b.lower()
        for phrase in fmt.forbidden_phrases:
            if phrase in low:
                _hard(issues, "bullet_point", "bullet_forbidden_phrase", f"第 {idx} 条五点含违禁语「{phrase}」")
        out.append(b)
    if len(bullets) != fmt.bullet_count:
        _hard(
            issues, "bullet_point", "bullet_count",
            f"五点描述必须恰好 {fmt.bullet_count} 条，当前 {len(bullets)} 条",
        )
    return out


def _fix_description(d: str, fmt: MarketFormat, issues: list[ListingIssue]) -> str:
    d = _strip_emoji(d, "product_description", issues)
    low = d.lower()
    for phrase in fmt.forbidden_phrases:
        if phrase in low:
            _hard(issues, "product_description", "description_forbidden_phrase", f"描述含违禁语「{phrase}」")
    if len(d) > fmt.description_max_chars:
        d = _truncate_chars(d, fmt.description_max_chars)
        _auto(issues, "product_description", "description_truncated", f"描述超 {fmt.description_max_chars} 字符，已截断")
    return d


def _fix_keywords(
    kw: str,
    fmt: MarketFormat,
    issues: list[ListingIssue],
    body_words: set[str],
    competitor_brands: list[str],
) -> str:
    k = _strip_emoji(kw, "generic_keyword", issues)
    normalized = _collapse(k)
    for sep, rep in _SEARCH_TERM_SEPARATORS.items():
        normalized = normalized.replace(sep, rep)
    normalized = _collapse(normalized)
    if normalized != k:
        _auto(issues, "generic_keyword", "search_terms_commas_normalized", "搜索词分隔符归一为空格")
    k = normalized

    brands_lower = {b.lower() for b in competitor_brands}
    kept: list[str] = []
    removed_asin = removed_brand = removed_dup = False
    for tok in k.split():
        low = tok.lower()
        if _ASIN_RE.match(tok):
            removed_asin = True
            continue
        if low in brands_lower:
            removed_brand = True
            continue
        if low in body_words:
            removed_dup = True
            continue
        kept.append(tok)
    if removed_asin:
        _auto(issues, "generic_keyword", "search_terms_asin_removed", "搜索词移除 ASIN")
    if removed_brand:
        _auto(issues, "generic_keyword", "search_terms_competitor_brand_removed", "搜索词移除竞品品牌词（ASIN 抑制风险）")
    if removed_dup:
        _auto(issues, "generic_keyword", "search_terms_duplicates_removed", "搜索词移除正文已覆盖词")

    # 字节预算：装不下的 token 丢弃；首个单独超预算的 token 按字节硬钳
    fitted: list[str] = []
    used = 0
    clamped = False
    for tok in kept:
        n = _byte_len(tok)
        sep = 1 if fitted else 0
        if used + sep + n <= fmt.search_terms_max_bytes:
            fitted.append(tok)
            used += sep + n
        elif not fitted and n > fmt.search_terms_max_bytes:
            while tok and _byte_len(tok) > fmt.search_terms_max_bytes:
                tok = tok[:-1]
            fitted.append(tok)
            used = _byte_len(tok)
            clamped = True
        else:
            clamped = True
    result = " ".join(fitted)
    if clamped:
        _auto(
            issues, "generic_keyword", "search_terms_truncated_to_byte_budget",
            f"搜索词超 {fmt.search_terms_max_bytes} 字节——超限整字段作废，已钳制到预算内",
        )
    return result


def validate_listing(
    output: ListingOutput,
    market: MarketConfig,
    competitor_brands: list[str] | None = None,
) -> ValidationResult:
    """校验并修复 LLM Listing 草稿（ 官方格式规范）。纯函数：返回新对象。

    competitor_brands 来自选品候选的竞品品牌（amazon_competitors.brand），
    用于搜索词层的品牌词剔除；None 表示无竞品上下文。
    """
    fmt = market.format
    issues: list[ListingIssue] = []
    fixed = output.model_copy(deep=True)

    fixed.item_name = _fix_title(fixed.item_name, fmt, issues)
    fixed.bullet_point = _fix_bullets(fixed.bullet_point, fmt, issues)
    fixed.product_description = _fix_description(fixed.product_description, fmt, issues)

    # product_type 是亚马逊 SP-API 上传层的必填分类键, 缺失会让上传返 400。
    # LLM 经常漏填 (契约里只写了示例, 没强约束), 校验层不能静默替它造一个
    # (不同品类 schema 完全不同, 猜错的代价比显式报错大)。只报不改。
    if not (fixed.product_type or "").strip():
        _hard(
            issues, "product_type", "product_type_missing",
            "product_type 缺失 — SP-API 上传必填, 需重生成或人工指定 (大写下划线式, 如 PET_FEEDER)",
        )

    body = " ".join([fixed.item_name, *fixed.bullet_point, fixed.product_description])
    body_words = set(body.lower().split())
    fixed.generic_keyword = _fix_keywords(
        fixed.generic_keyword, fmt, issues, body_words, list(competitor_brands or []),
    )

    return ValidationResult(
        output=fixed,
        issues=issues,
        hard_failed=any(i.severity == "hard_fail" for i in issues),
    )
