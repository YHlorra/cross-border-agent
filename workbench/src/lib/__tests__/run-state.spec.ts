import { describe, expect, it } from "vitest";
import { newChatRun, applyChatEvent } from "@/components/chat/chat-run-state";

describe("chat-run-state 归约器(迁移保真)", () => {
  it("newChatRun 初始化 running 态", () => {
    const s = newChatRun("宠物用品");
    expect(s.phase).toBe("running");
    expect(s.query).toBe("宠物用品");
  });

  it("未知事件优雅忽略(同一状态对象原样返回)", () => {
    const s = newChatRun("q");
    const after = applyChatEvent(s, {
      event: "approval_required",
      run_id: "r1",
    } as never);
    expect(after).toBe(s);
  });
});
