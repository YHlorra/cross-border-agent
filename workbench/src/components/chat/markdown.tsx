"use client";

import { memo } from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import rehypeHighlight from "rehype-highlight";

/**
 * Markdown renderer for agent output (Codex-style).
 * Typography lives in globals.css under `.md-body`; code blocks get
 * deterministic hljs token colors (dual-theme).
 */
export const Markdown = memo(function Markdown({ text }: { text: string }) {
  return (
    <div className="md-body">
      <ReactMarkdown
        remarkPlugins={[remarkGfm]}
        rehypePlugins={[[rehypeHighlight, { detect: false, ignoreMissing: true }]]}
      >
        {text}
      </ReactMarkdown>
    </div>
  );
});
