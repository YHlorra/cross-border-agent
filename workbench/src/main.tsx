import React from "react";
import ReactDOM from "react-dom/client";
import {
  createBrowserRouter,
  RouterProvider,
  Outlet,
} from "react-router-dom";
import { ThemeProvider } from "@appica/ui-react/providers/theme-provider";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import "@fontsource/noto-sans-sc/400.css";
import "@fontsource/noto-sans-sc/500.css";
import "@fontsource/noto-sans-sc/700.css";
import "./index.css";
import { Workbench } from "@/components/workbench";
import ConsoleLayout from "@/console/ConsoleLayout";
import ListingsPage from "@/console/listings/ListingsPage";
import { PipelinePage } from "@/console/pipeline/PipelinePage";
import ApprovalsPage from "@/console/approvals/ApprovalsPage";

const queryClient = new QueryClient({
  defaultOptions: { queries: { retry: 1, refetchOnWindowFocus: true } },
});

const router = createBrowserRouter([
  { path: "/", element: <Workbench /> },
  {
    path: "/console",
    element: <ConsoleLayout />,
    children: [
      { index: true, element: <ListingsPage /> },
      { path: "listings", element: <ListingsPage /> },
      { path: "pipeline", element: <PipelinePage /> },
      { path: "approvals", element: <ApprovalsPage /> },
    ],
  },
]);

ReactDOM.createRoot(document.getElementById("root")!).render(
  <React.StrictMode>
    <ThemeProvider defaultTheme="system">
      <QueryClientProvider client={queryClient}>
        <RouterProvider router={router} />
      </QueryClientProvider>
    </ThemeProvider>
  </React.StrictMode>,
);
