"""Prompt loader — strips YAML frontmatter and returns body + metadata."""
from __future__ import annotations

from pathlib import Path

_PROMPTS_DIR = Path(__file__).resolve().parent / "selection"


def load_prompt(name: str, subdir: str | None = None) -> tuple[dict, str]:
    """Load a prompt by its ``name:`` field (the frontmatter key).

    ``subdir`` selects a sibling directory under ``prompts/`` (e.g.
    ``"listing"`` → ``prompts/listing/<name>.md``); default stays in
    ``prompts/selection/``.

    Returns ``(metadata, body)`` where ``metadata`` is the parsed YAML
    frontmatter dict (with string values; no YAML lib needed for the simple
    shape we use).
    """
    base = _PROMPTS_DIR.parent / subdir if subdir else _PROMPTS_DIR
    path = base / f"{name}.md"
    if not path.exists():
        raise FileNotFoundError(f"Prompt not found: {path}")
    text = path.read_text(encoding="utf-8")

    if text.startswith("---"):
        end = text.find("\n---", 3)
        if end != -1:
            front = text[3:end].strip()
            body = text[end + 4 :].lstrip("\n")
            return _parse_frontmatter(front), body
    return {}, text


def _parse_frontmatter(front: str) -> dict:
    out: dict = {}
    for line in front.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if ":" in line:
            key, val = line.split(":", 1)
            out[key.strip()] = val.strip()
    return out


def load_selection_system_prompt() -> str:
    """选品 agent 系统提示词装配：agent.md（角色/工具/输出契约）+ 选品方法论
    skill 附录（prompts/skills/ecom-selection-method.md）。

    方法论以标准 skill 文件形式独立维护（与 general agent 的 load_skill 共用
    一个目录），但它在每次选品 run 都必需，因此由装配层全文注入而非依赖模型
    自觉调用 load_skill。
    """
    _, body = load_prompt("agent")
    _, method_skill = load_prompt("ecom-selection-method", subdir="skills")
    return f"{body}\n\n---\n\n# 附录：选品方法论\n\n{method_skill}"


def list_skills() -> list[dict]:
    """枚举 skills/ 目录下的可用领域 skill（name + description + 可选 slash 别名）。

    load_skill 工具的发现层：新增 skill 只需放 .md 文件进 prompts/skills/，
    零代码注册。``slash`` 字段（frontmatter 单行逗号分隔）是 backend 派生
    slash 路由的唯一真相源——前端 commands.ts 必须镜像这些 token，契约测试
    守住两面对齐。
    """
    skills_dir = _PROMPTS_DIR.parent / "skills"
    out: list[dict] = []
    if not skills_dir.exists():
        return out
    for path in sorted(skills_dir.glob("*.md")):
        try:
            meta, _ = load_prompt(path.stem, subdir="skills")
        except Exception:  # noqa: BLE001 — 单个坏文件不拖垮目录枚举
            continue
        if meta.get("name"):
            out.append(
                {
                    "name": meta["name"],
                    "description": meta.get("description", ""),
                    "slash": meta.get("slash", ""),
                }
            )
    return out


def load_skill(name: str) -> str | None:
    """按 name 读单个领域 skill 正文；未知名返回 None（调用方转结构化错误）。"""
    try:
        meta, body = load_prompt(name, subdir="skills")
    except FileNotFoundError:
        return None
    return body if meta.get("name") == name else None


def _skill_slash_routes() -> dict[str, str]:
    """token（lowercased）→ skill name，懒构建。

    来源是 skills/*.md frontmatter 的可选 ``slash:`` 字段（单行、逗号分隔），
    新 skill 文件带该字段即注册 slash 命令、零代码。冲突时 sorted 文件序
    先到先得（dict.setdefault 保证首条胜）。
    """
    routes: dict[str, str] = {}
    for entry in list_skills():
        slash = str(entry.get("slash") or "").strip()
        if not slash:
            continue
        for raw in slash.split(","):
            token = raw.strip().lower()
            if token:
                routes.setdefault(token, entry["name"])
    return routes


def slash_skill_appendix(query: str) -> str | None:
    """Codex 风格 slash 语义（ Addendum 4 + 5 解耦）：/token 命令的
    路由完全由 skill frontmatter 的 ``slash:`` 字段派生——追加 skill = 加
    一行 frontmatter；本函数无需修改。返回 None 表示非 slash 或未知 token
    （原样进 agent）。查询原文不剥 token——user_message 持久化保真。
    """
    trimmed = query.strip()
    if not trimmed.startswith("/"):
        return None
    token = trimmed[1:].split(maxsplit=1)[0].lower()
    skill_name = _skill_slash_routes().get(token)
    if skill_name is None:
        return None
    skill = load_skill(skill_name)
    if skill is None:
        return None
    return (
        f"# 附录：slash 命令注入——{skill_name}\n\n"
        f"用户通过 /{token} 命令显式发起了本次请求。斜杠是意图标记而非固定"
        "流水线开关：按以下方法论灵活执行；需要确定性产出（报告卡、草稿"
        "落库入列表）时调用对应领域 CLI 工具。\n\n"
        f"{skill}"
    )


__all__ = [
    "load_prompt",
    "load_selection_system_prompt",
    "list_skills",
    "load_skill",
    "slash_skill_appendix",
]