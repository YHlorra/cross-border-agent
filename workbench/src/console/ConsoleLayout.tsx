import { Layout, Menu, ConfigProvider, theme as antdTheme } from "antd";
import { useOutlet, useNavigate } from "react-router-dom";
import {
  IconTable,
  IconGauge,
  IconChecklist,
} from "@tabler/icons-react";
import { useTheme } from "@appica/ui-react/hooks/use-theme";
import { antdTokens, antdDarkTokens } from "./antdTokens";

const items = [
  { key: "listings", icon: <IconTable size={16} />, label: "Listing 管理" },
  { key: "pipeline", icon: <IconGauge size={16} />, label: "流水线看板" },
  { key: "approvals", icon: <IconChecklist size={16} />, label: "AI 审批" },
];

/** 控制台壳:antd 主题桥 + ProLayout 式布局(窄侧栏)。 */
export default function ConsoleLayout() {
  const outlet = useOutlet();
  const navigate = useNavigate();
  const { resolvedTheme } = useTheme();
  const dark = resolvedTheme === "dark";

  return (
    <ConfigProvider
      theme={{ ...(dark ? antdDarkTokens : antdTokens), algorithm: dark ? antdTheme.darkAlgorithm : antdTheme.defaultAlgorithm }}
    >
      <Layout style={{ minHeight: "100vh" }}>
        <Layout.Sider width={180} theme="light">
          <div style={{ padding: 16, fontWeight: 500 }}>店长控制台</div>
          <Menu
            mode="inline"
            defaultSelectedKeys={[location.hash.replace("#/console/", "") || "listings"]}
            onClick={({ key }) => navigate(`/console/${key}`)}
            items={items}
          />
        </Layout.Sider>
        <Layout.Content style={{ padding: 24 }}>{outlet}</Layout.Content>
      </Layout>
    </ConfigProvider>
  );
}
