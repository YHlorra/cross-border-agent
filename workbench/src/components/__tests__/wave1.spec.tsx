import { describe, expect, it, vi, beforeEach, afterEach } from "vitest";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import CompetitorCard from "@/components/competitor-card";
import { RiskTags, extractRiskTags } from "@/components/risk-tags";
import Sparkline from "@/components/charts/sparkline";
import { ProductCard } from "@/components/product-card";
import { ResultView } from "@/components/views/result-view";
import ListingEditor, {
  diffFields,
  EDITABLE_FIELDS,
} from "@/components/listing/editor";
import type { ScoredCandidate } from "@/lib/selection-types";

afterEach(cleanup);

describe("competitor-card", () => {
  it("渲染竞品行", () => {
    render(
      <CompetitorCard
        candidates={[
          { name: "A", price: 19.9, rating: 4.5, reviews: 120 },
          { name: "B" },
        ]}
      />,
    );
    expect(screen.getByText("A")).toBeTruthy();
    expect(screen.getByText("竞品对比")).toBeTruthy();
  });
  it("空数组不渲染", () => {
    const { container } = render(<CompetitorCard candidates={[]} />);
    expect(container.textContent).toBe("");
  });
});

describe("risk-tags", () => {
  it("字段映射纯函数", () => {
    const tags = extractRiskTags({ risk_flags: ["high:侵权风险", "low:季节性"] });
    expect(tags).toEqual([
      { level: "high", text: "侵权风险" },
      { level: "low", text: "季节性" },
    ]);
  });
  it("hard_failed → 高风险", () => {
    expect(extractRiskTags({ hard_failed: true })).toEqual([
      { level: "high", text: "硬性合规失败" },
    ]);
  });
});

describe("sparkline", () => {
  it("有序列挂载容器,空序列不渲染", () => {
    const { container, rerender } = render(<Sparkline series={[1, 2, 3]} />);
    expect(container.querySelector("div")).toBeTruthy();
    rerender(<Sparkline series={[]} />);
    expect(container.textContent).toBe("");
  });
});

const fakeCandidate: ScoredCandidate = {
  product_id: "p1",
  name_cn: "骨传导耳机",
  name_en: "Bone Conduction Headphones",
  source_price_cny: 68,
  target_price_usd: 32.99,
  price_gap_ratio: 3.2,
  category: "electronics_audio",
  review_count: 186,
  scores: [],
  total_score: 8.1,
  recommendation: "go",
  opportunities: ["蓝海类目"],
  risks: ["侵权风险", "季节性波动"],
  trend_series: [12000, 13500, 15000, 18000],
};

describe("product-card wave1 挂载", () => {
  it("有 risks 渲染风险标签,有序列渲染 sparkline 容器", () => {
    const { container } = render(
      <ProductCard product={fakeCandidate} index={0} onClick={() => {}} />,
    );
    expect(screen.getByText("侵权风险")).toBeTruthy();
    expect(screen.getByText("季节性波动")).toBeTruthy();
    // jsdom 无 canvas:断言 sparkline 容器(28px 高)挂载即可,不做像素断言
    expect(container.querySelector('div[style*="height: 28px"]')).toBeTruthy();
  });
  it("无 risks 无序列不渲染对应块", () => {
    const { container } = render(
      <ProductCard
        product={{ ...fakeCandidate, risks: [], trend_series: undefined }}
        index={0}
        onClick={() => {}}
      />,
    );
    expect(screen.queryByText("侵权风险")).toBeNull();
    expect(container.querySelector('div[style*="height: 28px"]')).toBeNull();
  });
});

describe("result-view wave1 挂载", () => {
  it("渲染候选竞品一览表(行=候选映射)", () => {
    render(
      <ResultView
        query="耳机"
        report={null}
        candidates={[fakeCandidate]}
        decision="go"
      />,
    );
    expect(screen.getByText("候选竞品一览（Amazon 数据集）")).toBeTruthy();
    expect(screen.getByText("Bone Conduction Headphones")).toBeTruthy();
    expect(screen.getByText("32.99")).toBeTruthy();
  });
  it("无候选不渲染竞品表", () => {
    render(
      <ResultView query="耳机" report={null} candidates={[]} decision="no_result" />,
    );
    expect(screen.queryByText("候选竞品一览（Amazon 数据集）")).toBeNull();
  });
});

describe("listing editor diff 保存", () => {
  beforeEach(() => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => new Response(JSON.stringify({ ok: true }), { status: 200 })),
    );
  });
  afterEach(() => vi.unstubAllGlobals());

  it("diffFields 只回传变更字段", () => {
    const changed = diffFields(
      { item_name: "a", bullet_point: ["x"], product_type: "t" },
      { item_name: "b", bullet_point: ["x"], product_type: "t2" },
    );
    expect(changed).toEqual({ item_name: "b", product_type: "t2" });
  });

  it("白名单外字段不渲染;保存只发白名单内变更项", async () => {
    const qc = new QueryClient();
    render(
      <QueryClientProvider client={qc}>
        <ListingEditor
          runId="r1"
          listing={{ item_name: "旧名", product_type: "HOME", subject_matter: "x" }}
        />
      </QueryClientProvider>,
    );
    // product_type / subject_matter 不进编辑器
    expect(screen.queryByText("product_type")).toBeNull();
    expect(screen.queryByText("subject_matter")).toBeNull();
    // 未修改时保存禁用
    const save = screen.getByText("无修改") as HTMLButtonElement;
    expect(save.disabled).toBe(true);
    // 修改 item_name → 只发 item_name
    fireEvent.change(screen.getByDisplayValue("旧名"), {
      target: { value: "新名" },
    });
    fireEvent.click(screen.getByText(/保存修改/));
    await vi.waitFor(() => {
      expect(vi.mocked(fetch)).toHaveBeenCalledTimes(1);
    });
    const [, init] = vi.mocked(fetch).mock.calls[0];
    expect(JSON.parse(String(init?.body))).toEqual({ fields: { item_name: "新名" } });
  });

  it("EDITABLE_FIELDS 与服务端白名单语义一致(不 product_type 族)", () => {
    expect(EDITABLE_FIELDS.has("item_name")).toBe(true);
    expect(EDITABLE_FIELDS.has("image_url")).toBe(true);
    expect(EDITABLE_FIELDS.has("product_type")).toBe(false);
    expect(EDITABLE_FIELDS.has("subject_matter")).toBe(false);
    expect(EDITABLE_FIELDS.has("target_audience")).toBe(false);
  });
});
