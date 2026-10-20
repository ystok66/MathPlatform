import { NextRequest } from "next/server";
import languages from "../../../../../packages/i18n/languages.json";

export const dynamic = "force-dynamic";

const DEFAULT_API_BASE = "http://127.0.0.1:8001";
type ProxyContext = { params: Promise<{ path?: string[] }> };

function apiBase(): string {
  return (process.env.MATH_PLATFORM_API_BASE || process.env.NEXT_PUBLIC_API_BASE || DEFAULT_API_BASE).replace(/\/+$/, "");
}

function filteredHeaders(source: Headers): Headers {
  const headers = new Headers(source);
  for (const key of ["host", "connection", "content-length", "transfer-encoding", "keep-alive"]) {
    headers.delete(key);
  }
  return headers;
}

async function proxy(request: NextRequest, context: ProxyContext) {
  const params = await context.params;
  const path = (params.path || []).map(encodeURIComponent).join("/");
  const sourceUrl = new URL(request.url);
  const target = `${apiBase()}/api/${path}${sourceUrl.search}`;
  const headers = filteredHeaders(request.headers);
  const requestedLanguage = request.headers.get("X-Math-Platform-Language")?.trim().toLowerCase();
  headers.set("X-Math-Platform-Language", languages.languages.find(item => item.enabled && item.code.toLowerCase() === requestedLanguage)?.code || languages.default_language);
  const init: RequestInit & { duplex?: "half" } = {
    method: request.method,
    headers,
    cache: "no-store",
  };
  if (!["GET", "HEAD"].includes(request.method)) {
    init.body = await request.arrayBuffer();
    init.duplex = "half";
  }
  const upstream = await fetch(target, init);
  const responseHeaders = filteredHeaders(upstream.headers);
  return new Response(upstream.body, {
    status: upstream.status,
    statusText: upstream.statusText,
    headers: responseHeaders,
  });
}

export async function GET(request: NextRequest, context: ProxyContext) {
  return proxy(request, context);
}

export async function POST(request: NextRequest, context: ProxyContext) {
  return proxy(request, context);
}

export async function PUT(request: NextRequest, context: ProxyContext) {
  return proxy(request, context);
}

export async function PATCH(request: NextRequest, context: ProxyContext) {
  return proxy(request, context);
}

export async function DELETE(request: NextRequest, context: ProxyContext) {
  return proxy(request, context);
}
