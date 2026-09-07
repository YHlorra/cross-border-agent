import { describe, expect, it } from "vitest";

import { COMMANDS, filterCommands, parseCommand, splitCommandQuery } from "@/lib/commands";

describe("commands slash 注册表", () => {
  it("COMMANDS 至少 5 条 chat + 2 条 local", () => {
    const chat = COMMANDS.filter((c) => c.kind === "chat");
    const local = COMMANDS.filter((c) => c.kind === "local");
    expect(chat.length).toBeGreaterThanOrEqual(5);
    expect(local.length).toBeGreaterThanOrEqual(2);
  });

  it("parseCommand: zh label / latin id / 额外 alias 都路由到同一条命令", () => {
    const a = parseCommand("/选品 空气炸锅 预算 500");
    expect(a?.command.id).toBe("selection");
    expect(a?.rest).toBe("空气炸锅 预算 500");

    const b = parseCommand("/selection pet feeder");
    expect(b?.command.id).toBe("selection");

    const c = parseCommand("/生图 主图白底");
    expect(c?.command.id).toBe("image");

    const d = parseCommand("/差评 竞品");
    expect(d?.command.id).toBe("review");

    const e = parseCommand("/平台规则 Amazon DE");
    expect(e?.command.id).toBe("rules");

    const f = parseCommand("/listing");
    expect(f?.command.id).toBe("listing");
    expect(f?.rest).toBe("");
  });

  it("parseCommand: local 命令（/new、/compact）kind=local", () => {
    expect(parseCommand("/new")?.command.kind).toBe("local");
    expect(parseCommand("/compact")?.command.kind).toBe("local");
  });

  it("parseCommand: 未知 token 与普通文本返回 null", () => {
    expect(parseCommand("/help x")).toBeNull();
    expect(parseCommand("帮我选品")).toBeNull();
    expect(parseCommand("")).toBeNull();
  });

  it("filterCommands: 空前缀返全表；/图 命中 image（label「生图」含「图」）", () => {
    expect(filterCommands("/").length).toBe(COMMANDS.length);
    const byTu = filterCommands("/图").map((c) => c.id);
    expect(byTu).toContain("image");
  });

  it("splitCommandQuery: 保留已输入参数（palette pick 后补全 token）", () => {
    const r = splitCommandQuery("/选 空气炸锅");
    expect(r.token).toBe("/选");
    expect(r.rest).toBe("空气炸锅");
  });
});
