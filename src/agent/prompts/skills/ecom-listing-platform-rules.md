---
name: ecom-listing-platform-rules
description: 电商平台规则手册——为不同平台（亚马逊/TikTok Shop/Shopify）写或审 Listing、判断某句文案是否踩合规红线、或需要某站点语言与字符上限时使用：三平台数字规则表、claim 合规禁令、站点语言匹配、与本项目代码校验层的硬编码映射
source: reference_projects/listforge-prompts、zach-amazon-skills、nexscope-amazon-skills（本地快照，MIT）+ docs/11
version: "1.0"
slash: 平台规则, rules
---

# 平台规则手册（硬约束，不是软建议）

平台规则的本质是**机器可校验的数字与禁令**，不是写作风格建议。本项目里这些
规则由代码强制执行：字符数超限自动截断、违禁语触发 hard_fail 打回重写——
prompt 只是让第一稿就长在规则内，减少返工；真正的防线在校验层。

## 三平台规则表

| 项 | 亚马逊 | TikTok Shop | Shopify（独立站） |
|----|--------|-------------|-------------------|
| 买家心智 | 货比三家的比价模式 | 短视频信息流里刷到 | 品牌认知后的到店浏览 |
| 标题 | ≤75 字符（2026-07 新规），主关键词+最强差异点必须前置 | 60-100 字符，钩子+利益+品类词，口语化可用 | 60-90 字符，品牌引领，质感语气 |
| 五点 | 每条 ≤250 字符（平台允许更长但买家只扫读），大写钩子开头 | 每条 60-110 字符，移动端优先，一屏内读完 | 每条 80-180 字符，"Key Features" 列表式，不用大写钩子 |
| 描述 | 痛点开头 → 差异化 → 佐证（不编造评价）→ 软性行动号召 | 120-220 词，第二人称"你"，短句，创作者式收尾 | 200-380 词，品牌叙事：场景开场 → 特性 → 材质规格 → 保养关怀 |
| 搜索词 | ≤250 字节，小写、去重、禁竞品品牌 | 趋势搜索短语，含问题式查询 | 按 SEO 元关键词 + 集合标签处理 |

**亚马逊标题黄金区**：历史上标题可写 150-200 字符、手机端只展示前 75 字符；
2026-07 新规后整条标题就是黄金区——主关键词、核心规格、最强差异点全部前置，
没有"后面再补"的空间。

## Claim 合规禁令（全平台，代码层强制）

以下类别一律不写——不是语气建议，是平台会抑制 ASIN、卖家会吃绩效的硬红线：

| 类别 | 示例禁语 | 说明 |
|------|----------|------|
| 价格折扣 | 50% off、on sale、best price、was $X | 价格类信息不属于文案，属于价格字段 |
| 物流承诺 | free shipping、ships next day、Prime eligible | 由配送设置表达，文案承诺越权 |
| 库存紧迫 | limited stock、selling fast、only 3 left | 人为紧迫感属违规操纵 |
| 质保认证 | FDA approved、CE certified、lifetime warranty、money-back guarantee | 卖家明确提供的认证才可写；拿不准就不写 |
| 主观断言 | #1、top rated、amazing、ultimate、perfect、revolutionary | 不可证实的断言 |
| 流量作弊 | 编造的评价数/星级/奖项、竞品品牌名、ASIN | 搜索词层代码自动剔除 |

## 站点语言匹配（强制）

输出语言必须匹配目标站点，与卖家沟通语言无关：US/UK/AU/CA→英文、
DE→德文、FR→法文、IT→意大利文、ES/MX→西班牙文、JP→日文、BR→葡萄牙文。
本地化不是翻译——详见 ecom-listing-copywriting 的本地化章节。

## 本项目硬编码映射（规则 → 代码）

新平台/新站点 = 在 `data/selection/marketplaces.json` 加一段配置，**不改校验器代码**：

| 规则 | marketplaces.json 键 | 校验器行为（src/agent/listing/validate.py） |
|------|----------------------|---------------------------------------------|
| 标题字符上限 | `format.title_max_chars`（US=75） | 超长按词边界截断，留 auto_fixed issue |
| 五点条数 | `format.bullet_count`（US=5） | 条数不符 → hard_fail |
| 五点长度 | `format.bullet_min_chars` / `bullet_max_chars` | 过短 hard_fail；超长截断 |
| 描述上限 | `format.description_max_chars`（US=2000） | 超长截断 |
| 搜索词字节预算 | `format.search_terms_max_bytes`（US=250） | 超限整字段作废，钳制到预算内 |
| 禁用字符 | `format.title_forbidden_chars`（! $? 等） | 替换为空格 |
| claim 禁令 | `format.forbidden_phrases` | 标题/五点/描述命中 → hard_fail（只报不改，改语义走重生成） |
| 主观断言词 | `format.subjective_terms` | 标题命中 → hard_fail |
| 搜索词去重/品牌剔除 | （代码内建） | 自动剔除 ASIN、竞品品牌词、与正文重复词 |

写作纪律：第一稿就按上表数字写；收到 hard_failed=true 时按 issue 的
code 逐条修正后再提交，不要原样重提赌运气。
