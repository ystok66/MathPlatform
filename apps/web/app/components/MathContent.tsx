"use client";

import katex from "katex";

type Props = {
  source: string;
};

const FORBIDDEN_TAG_PATTERN = /<\/?(script|iframe|object|embed|link|meta|base|style)\b[^>]*>/gi;
const EVENT_HANDLER_ATTR_PATTERN = /\s+on[a-z]+\s*=\s*(".*?"|'.*?'|[^\s>]+)/gi;
const DANGEROUS_URL_ATTR_PATTERN =
  /\s+(href|src|xlink:href)\s*=\s*(?:(['"])\s*(?:javascript|vbscript|data|file|blob)\s*:[\s\S]*?\2|(?:javascript|vbscript|data|file|blob)\s*:[^\s>]+)/gi;
const UNSAFE_CONTROL_PATTERN = /[\u0000-\u0008\u000B\u000C\u000E-\u001F\u007F]/g;
const MAX_MATH_SOURCE_CHARS = 20000;

export function escapeHtml(value: string): string {
  return value
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;")
    .replace(/'/g, "&#39;");
}

function normalizeTextInput(value: string): string {
  const cleaned = value.replace(UNSAFE_CONTROL_PATTERN, "");
  if (cleaned.length <= MAX_MATH_SOURCE_CHARS) {
    return cleaned;
  }
  return `${cleaned.slice(0, MAX_MATH_SOURCE_CHARS)}\n[content truncated]`;
}

export function sanitizeRenderedHtml(value: string): string {
  return value
    .replace(FORBIDDEN_TAG_PATTERN, "")
    .replace(EVENT_HANDLER_ATTR_PATTERN, "")
    .replace(DANGEROUS_URL_ATTR_PATTERN, "");
}

function renderText(value: string): string {
  return escapeHtml(value)
    .replace(/\*\*(.+?)\*\*/g, "<strong>$1</strong>")
    .replace(/\n/g, "<br />");
}

function renderMath(expr: string, displayMode: boolean): string {
  return katex.renderToString(expr, {
    displayMode,
    throwOnError: false,
    strict: false,
    trust: false,
  });
}

function isVerticalArithmeticBlock(value: string): boolean {
  const lines = value.split("\n").map((line) => line.trim()).filter(Boolean);
  const hasRule = lines.some((line) => /^[-\u2014]{3,}$/.test(line));
  const hasOperatorLine = lines.some((line) => /^[+\-\u00D7xX\u00F7]\s*/.test(line));
  const hasPlaceholders = /[\u25A1\u25A2_]/.test(value);
  return lines.length >= 3 && (hasRule || hasOperatorLine) && hasPlaceholders;
}

function renderCodeBlock(lang: string, value: string): string {
  const className = isVerticalArithmeticBlock(value) ? "mathTextBlock verticalArithmeticBlock" : "mathTextBlock";
  const label = lang ? ` data-lang="${escapeHtml(lang)}"` : "";
  return `<pre class="${className}"${label}><code>${escapeHtml(value.replace(/^\n|\n$/g, ""))}</code></pre>`;
}

function renderInlineMathMarkdown(source: string): string {
  const tokenPattern = /(\\\[[\s\S]+?\\\]|\\\([\s\S]+?\\\)|\$\$[\s\S]+?\$\$|\$[^$\n]+?\$)/g;
  let cursor = 0;
  let html = "";
  for (const match of source.matchAll(tokenPattern)) {
    const token = match[0];
    const index = match.index ?? 0;
    html += renderText(source.slice(cursor, index));
    if (token.startsWith("$$")) {
      html += renderMath(token.slice(2, -2).trim(), true);
    } else if (token.startsWith("\\[")) {
      html += renderMath(token.slice(2, -2).trim(), true);
    } else if (token.startsWith("\\(")) {
      html += renderMath(token.slice(2, -2).trim(), false);
    } else {
      html += renderMath(token.slice(1, -1).trim(), false);
    }
    cursor = index + token.length;
  }
  html += renderText(source.slice(cursor));
  return html;
}

export function renderMathMarkdown(source: string): string {
  const normalized = normalizeTextInput(source).replace(/\r\n/g, "\n").trim();
  const fencePattern = /```([a-zA-Z0-9_-]*)\n([\s\S]*?)```/g;
  let cursor = 0;
  let html = "";
  for (const match of normalized.matchAll(fencePattern)) {
    const index = match.index ?? 0;
    html += renderInlineMathMarkdown(normalized.slice(cursor, index));
    html += renderCodeBlock(match[1] || "", match[2] || "");
    cursor = index + match[0].length;
  }
  html += renderInlineMathMarkdown(normalized.slice(cursor));
  return sanitizeRenderedHtml(html);
}

export function MathContent({ source }: Props) {
  return <div className="mathContent" dangerouslySetInnerHTML={{ __html: renderMathMarkdown(source) }} />;
}
