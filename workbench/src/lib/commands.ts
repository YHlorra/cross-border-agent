// Slash-command registry — the single-conversation IA's trigger layer
// ( + Addendum 4/5). Domain slash commands take the SAME /chat path
// as plain text: the server injects the matching skill as a system-prompt
// appendix and the agent flexibly drives the domain CLI tools.
//
// Extension contract ( decoupling — add a command with minimal code):
// - chat command  → add ONE entry here + a `slash:` line in the skill
//   frontmatter (backend routes derive from it). A pytest contract test
//   (tests/test_domain_skills.py) keeps both token sets aligned.
// - local command → add ONE entry here + ONE branch in Workbench's
//   runLocalCommand (pure frontend action, never routed to /chat).
//
// Command ids stay latin (wire-stable); labels are zh for display.
// parseCommand matches the zh label, the latin id, and any extra aliases
// (all case-insensitive; the palette derives from COMMANDS automatically).

export type CommandKind = "chat" | "local";

export interface CommandDef {
  id: string;
  label: string;
  usage: string;
  description: string;
  /** chat = send text to /chat (server injects the skill appendix);
   *  local = pure frontend action, handled by runLocalCommand. */
  kind: CommandKind;
  /** extra slash tokens beyond the label and id. */
  aliases?: string[];
}

export const COMMANDS: CommandDef[] = [
  {
    id: "new",
    label: "新会话",
    usage: "/new",
    description: "开一个新会话（运行中的会话继续在后台跑）",
    kind: "local",
  },
  {
    id: "compact",
    label: "压缩",
    usage: "/compact",
    description: "压缩当前会话历史——保留要点，释放后续上下文预算",
    kind: "local",
  },
  {
    id: "selection",
    label: "选品",
    usage: "/选品 <需求描述>",
    description: "注入选品方法论到上下文——agent 灵活检索探索，完整需求自动调用选品 CLI 出报告卡",
    kind: "chat",
  },
  {
    id: "listing",
    label: "Listing",
    usage: "/listing [商品名]",
    description: "注入 Listing 文案方法论到上下文——agent 按方法论起草或调 Listing CLI，草稿入列表",
    kind: "chat",
  },
  {
    id: "image",
    label: "生图",
    usage: "/生图 <商品/场景描述>",
    description: "注入商品生图 SOP 到上下文——按六要素框架构图并调用绘蛙生图",
    kind: "chat",
  },
  {
    id: "review",
    label: "差评",
    usage: "/差评 <品类/竞品>",
    description: "注入差评→卖点反向链路到上下文——A/B 对照采集、阈值判定、投诉转卖点",
    kind: "chat",
  },
  {
    id: "rules",
    label: "平台规则",
    usage: "/平台规则 <平台/站点>",
    description: "注入平台规则手册到上下文——三平台数字规则、claim 禁令、站点语言匹配",
    kind: "chat",
  },
];

/** token (lowercased) → command. Derived from COMMANDS: the zh label, the
 *  latin id, and any extra aliases all route to the same command. */
const TOKENS: Record<string, string> = (() => {
  const out: Record<string, string> = {};
  for (const c of COMMANDS) {
    for (const token of [c.label, c.id, ...(c.aliases ?? [])]) {
      out[token.toLowerCase()] = c.id;
    }
  }
  return out;
})();

export interface ParsedCommand {
  command: CommandDef;
  rest: string;
}

/** Parse "/选品 蓝牙耳机" → { command: selection, rest: "蓝牙耳机" }.
 *  Returns null for plain text (no leading "/") or an unknown token. */
export function parseCommand(text: string): ParsedCommand | null {
  const trimmed = text.trim();
  if (!trimmed.startsWith("/")) return null;
  const m = trimmed.slice(1).match(/^(\S+)\s*([\s\S]*)$/);
  if (!m) return null;
  const id = TOKENS[m[1].toLowerCase()];
  if (!id) return null;
  const command = COMMANDS.find((c) => c.id === id);
  if (!command) return null;
  return { command, rest: m[2].trim() };
}

/** Palette items for a live "/…" query — empty-prefix shows all commands. */
export function filterCommands(query: string): CommandDef[] {
  const body = query.trim().slice(1).toLowerCase();
  if (!body) return COMMANDS;
  return COMMANDS.filter(
    (c) =>
      c.label.toLowerCase().includes(body) ||
      c.id.includes(body) ||
      (c.aliases ?? []).some((a) => a.toLowerCase().includes(body)),
  );
}

/** Split a live "/…" query into its command token and the remainder after
 *  the first whitespace — used to preserve typed args when the user picks a
 *  command from the palette ("/选" + selection → "/选品 "). */
export function splitCommandQuery(query: string): { token: string; rest: string } {
  const m = query.match(/^(\S*)(?:\s([\s\S]*))?$/);
  return { token: m?.[1] ?? query, rest: m?.[2] ?? "" };
}
