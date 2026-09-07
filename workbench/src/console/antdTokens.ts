/**
 * DESIGN.md → antd ConfigProvider token 桥( 视觉 change 的唯一 antd 触达点)。
 * 值与根目录 DESIGN.md(Stripe-Classic-Blurple)一致。
 */
import type { ThemeConfig } from "antd";

export const antdTokens: ThemeConfig = {
  token: {
    colorPrimary: "#6772e5",
    colorInfo: "#6772e5",
    colorSuccess: "#1b7a4e",
    colorWarning: "#9a6700",
    colorError: "#cd3d64",
    colorTextBase: "#32325d",
    colorText: "#32325d",
    colorTextSecondary: "#525f7f",
    colorTextTertiary: "#8898aa",
    colorBgLayout: "#f6f9fc",
    colorBgContainer: "#ffffff",
    colorBorder: "#e0e5f0",
    colorBorderSecondary: "#eef2f7",
    borderRadius: 4,
    fontFamily:
      '-apple-system, BlinkMacSystemFont, "SF Pro Text", "PingFang SC", "Noto Sans SC", "Microsoft YaHei", sans-serif',
  },
  components: {
    Layout: { headerBg: "#ffffff", siderBg: "#ffffff" },
    Table: { headerBg: "#f6f9fc" },
  },
};

export const antdDarkTokens: ThemeConfig = {
  token: {
    ...antdTokens.token,
    colorPrimary: "#7a85ff",
    colorTextBase: "#e6ebf1",
    colorBgLayout: "#0f1222",
    colorBgContainer: "#1a1f36",
    colorBorder: "#2a3050",
  },
};
