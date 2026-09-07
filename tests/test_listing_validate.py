"""SEAM-L1 tests: validate_listing — Amazon format compliance for LLM drafts.

Constraints come from data/selection/marketplaces.json: title ≤75
chars (2026-07-27 rule), exactly 5 bullets of 10-255 chars without trailing
punctuation, description ≤2000, backend search terms ≤250 bytes — exceeding
the byte budget makes Amazon ignore the whole field, so the validator must
clamp instead of warn. Mechanical violations are auto-fixed with a visible
issue; content-level violations (promo / subjective wording) are hard
failures because removing them would change meaning. No LLM involved.
"""
from __future__ import annotations

from agent.listing.schema import ListingOutput, MarketConfig, MarketProfile
from agent.listing.validate import validate_listing


def _market(**format_overrides: object) -> MarketConfig:
    fmt: dict = {
        "title_max_chars": 75,
        "bullet_count": 5,
        "bullet_min_chars": 10,
        "bullet_max_chars": 255,
        "description_max_chars": 2000,
        "search_terms_max_bytes": 250,
        "title_forbidden_chars": ["!", "$", "?", "_", "{", "}", "^", "¬", "¦"],
        "forbidden_phrases": [
            "free shipping", "best seller", "bestseller", "#1", "top rated",
            "hot item", "money-back guarantee", "money back", "risk-free",
            "risk free", "fda approved",
        ],
        "subjective_terms": [
            "amazing", "greatest", "perfect", "incredible", "revolutionary",
            "game-changing", "best ever",
        ],
    }
    fmt.update(format_overrides)
    return MarketConfig(
        code="US",
        format=fmt,  # type: ignore[arg-type]
        profile=MarketProfile(
            marketplace_id="ATVPDKIKX0DER",
            language_tag="en_US",
            currency_code="USD",
            condition_default="new_new",
            fulfillment_channel_default="DEFAULT",
        ),
    )


def _output(**overrides: object) -> ListingOutput:
    base: dict = {
        "item_name": "Smart Pet Feeder Automatic Dog Cat Food Dispenser with Timer",
        "bullet_point": [
            "PROGRAMMABLE MEAL TIMES: schedule up to 4 meals per day for your pet",
            "APP CONTROL: feed your pet remotely with the wifi enabled phone app",
            "STAINLESS STEEL BOWL: food grade removable bowl is easy to clean",
            "DUAL POWER SUPPLY: usb or 3 D-cell batteries keep meals on schedule",
            "10S VOICE RECORDING: call your pet to eat with your own voice",
        ],
        "product_description": (
            "Automatic pet feeder keeps your cat or dog fed on schedule even "
            "when you are away. Programmable timers, dual power supply and a "
            "removable stainless steel bowl make daily feeding simple."
        ),
        "generic_keyword": "kibble auto treat camera monitor",
    }
    base.update(overrides)
    return ListingOutput(**base)  # type: ignore[arg-type]


def _codes(result: object) -> set[str]:
    return {i.code for i in result.issues}  # type: ignore[attr-defined]


def _hard_codes(result: object) -> set[str]:
    return {
        i.code
        for i in result.issues  # type: ignore[attr-defined]
        if i.severity == "hard_fail"
    }


# --- clean path -------------------------------------------------------------

def test_clean_listing_passes_without_issues() -> None:
    r = validate_listing(_output(), _market())
    assert r.issues == []
    assert not r.hard_failed
    assert r.output.item_name == "Smart Pet Feeder Automatic Dog Cat Food Dispenser with Timer"
    assert len(r.output.bullet_point) == 5


def test_validation_returns_new_object_original_untouched() -> None:
    out = _output()
    r = validate_listing(out, _market())
    assert r.output is not out


def test_title_at_exact_limit_untouched() -> None:
    title = "A" * 75
    r = validate_listing(_output(item_name=title), _market())
    assert r.output.item_name == title
    assert r.issues == []


# --- title ------------------------------------------------------------------

def test_title_over_limit_truncated_at_word_boundary() -> None:
    title = "Word " * 20  # 100 chars
    r = validate_listing(_output(item_name=title), _market())
    assert "title_truncated" in _codes(r)
    issue = next(i for i in r.issues if i.code == "title_truncated")
    assert issue.severity == "auto_fixed"
    assert len(r.output.item_name) <= 75
    assert r.output.item_name.endswith("Word")  # no partial word kept


def test_title_forbidden_char_stripped() -> None:
    r = validate_listing(_output(item_name="Smart Pet Feeder $39 Deal!"), _market())
    assert "title_forbidden_char_removed" in _codes(r)
    assert "$" not in r.output.item_name
    assert "!" not in r.output.item_name
    assert r.output.item_name == "Smart Pet Feeder 39 Deal"


def test_title_promo_phrase_is_hard_fail_not_rewritten() -> None:
    original = "Best Seller Smart Pet Feeder for Dogs"
    r = validate_listing(_output(item_name=original), _market())
    assert "title_forbidden_phrase" in _hard_codes(r)
    assert r.hard_failed
    # 内容级违规不改写文本 — 修正走重生成或人工编辑
    assert r.output.item_name == original


def test_title_subjective_term_is_hard_fail() -> None:
    r = validate_listing(_output(item_name="Amazing Smart Pet Feeder"), _market())
    assert "title_subjective_term" in _hard_codes(r)
    assert r.hard_failed


# --- bullets ----------------------------------------------------------------

def test_bullet_count_must_match_hard_fail() -> None:
    r = validate_listing(_output(bullet_point=["x" * 30] * 4), _market())
    assert "bullet_count" in _hard_codes(r)
    assert r.hard_failed


def test_bullet_over_max_truncated() -> None:
    long_bullet = "Long " * 80  # 400 chars
    r = validate_listing(_output(bullet_point=[long_bullet] * 5), _market())
    assert "bullet_truncated" in _codes(r)
    assert all(len(b) <= 255 for b in r.output.bullet_point)


def test_bullet_too_short_hard_fail() -> None:
    bullets = ["Feeder"] + ["B" * 30] * 4
    r = validate_listing(_output(bullet_point=bullets), _market())
    assert "bullet_too_short" in _hard_codes(r)


def test_bullet_trailing_punctuation_stripped() -> None:
    bullets = ["Easy to clean stainless steel bowl."] + ["B" * 30] * 4
    r = validate_listing(_output(bullet_point=bullets), _market())
    assert "bullet_trailing_punctuation_removed" in _codes(r)
    assert r.output.bullet_point[0] == "Easy to clean stainless steel bowl"


def test_bullet_emoji_stripped() -> None:
    bullets = ["Bowl is easy to clean 🐶 always"] + ["B" * 30] * 4
    r = validate_listing(_output(bullet_point=bullets), _market())
    assert "emoji_removed" in _codes(r)
    assert "🐶" not in r.output.bullet_point[0]


def test_bullet_refund_guarantee_hard_fail() -> None:
    bullets = ["MONEY-BACK GUARANTEE if your pet dislikes it"] + ["B" * 30] * 4
    r = validate_listing(_output(bullet_point=bullets), _market())
    assert "bullet_forbidden_phrase" in _hard_codes(r)


# --- description ------------------------------------------------------------

def test_description_over_limit_truncated() -> None:
    r = validate_listing(_output(product_description="Sentence. " * 230), _market())
    assert "description_truncated" in _codes(r)
    assert len(r.output.product_description) <= 2000


def test_description_forbidden_phrase_hard_fail() -> None:
    desc = "Great feeder for your pet. Ships with free shipping today."
    r = validate_listing(_output(product_description=desc), _market())
    assert "description_forbidden_phrase" in _hard_codes(r)
    assert r.hard_failed


# --- backend search terms ---------------------------------------------------

def test_search_terms_commas_normalized() -> None:
    # 用正文里没有的词, 隔离验证逗号归一(否则先被正文去重规则剥掉)
    r = validate_listing(_output(generic_keyword="kitten, puppy ,treats"), _market())
    assert "search_terms_commas_normalized" in _codes(r)
    assert r.output.generic_keyword == "kitten puppy treats"


def test_search_terms_body_duplicates_removed() -> None:
    # "automatic"/"feeder" 在标题里, "wifi" 在五点里 — 后台搜索词不得重复正文
    r = validate_listing(_output(generic_keyword="automatic feeder wifi camera"), _market())
    assert "search_terms_duplicates_removed" in _codes(r)
    assert r.output.generic_keyword == "camera"


def test_search_terms_competitor_brand_removed() -> None:
    r = validate_listing(
        _output(generic_keyword="petsafe camera alternative"),
        _market(),
        competitor_brands=["PetSafe"],
    )
    assert "search_terms_competitor_brand_removed" in _codes(r)
    assert r.output.generic_keyword == "camera alternative"


def test_search_terms_asin_removed() -> None:
    r = validate_listing(_output(generic_keyword="B08N5WRWNW camera"), _market())
    assert "search_terms_asin_removed" in _codes(r)
    assert r.output.generic_keyword == "camera"


def test_search_terms_cjk_byte_budget_clamped() -> None:
    # 90 个 CJK 字符 = 270 UTF-8 字节 > 250 — 单 token 超预算按字节硬钳,
    # 后续 token 无预算即丢弃; 超限整字段作废, 必须钳到预算内。
    r = validate_listing(_output(generic_keyword="字" * 90 + " tailword"), _market())
    assert "search_terms_truncated_to_byte_budget" in _codes(r)
    assert r.output.generic_keyword == "字" * 83  # 83*3 = 249 bytes


# --- aggregation ------------------------------------------------------------

def test_multiple_hard_fails_aggregate() -> None:
    r = validate_listing(
        _output(item_name="Best Seller Feeder", bullet_point=["x" * 30] * 3),
        _market(),
    )
    hard = _hard_codes(r)
    assert {"title_forbidden_phrase", "bullet_count"} <= hard
    assert r.hard_failed


def test_competitor_brands_none_is_tolerated() -> None:
    r = validate_listing(_output(generic_keyword="kibble auto treat"), _market())
    assert r.issues == []
