import { languageFetch, subscribeLanguage } from "./language";
// Narrow fetch-based SSE adapter: native EventSource cannot send the language header.
export class LanguageEventSource {
  onerror: ((event: Event) => void) | null = null;
  private target = new EventTarget();
  private controller: AbortController | null = null;
  private stopped = false;
  private lastEventId = "";
  private unsubscribe: () => void;
  constructor(private url: string) {
    this.unsubscribe = subscribeLanguage(() => { this.controller?.abort(); if (!this.stopped) void this.open(); });
    void this.open();
  }
  addEventListener(type: string, callback: (event: MessageEvent) => void): void { this.target.addEventListener(type, callback as EventListener); }
  close(): void { this.stopped = true; this.controller?.abort(); this.unsubscribe(); }
  private async open(): Promise<void> {
    const controller = new AbortController();
    this.controller = controller;
    let reader: ReadableStreamDefaultReader<Uint8Array> | undefined;
    try {
      const headers = new Headers({ Accept: "text/event-stream" });
      if (this.lastEventId) headers.set("Last-Event-ID", this.lastEventId);
      const response = await languageFetch(this.url, { headers, signal: controller.signal, cache: "no-store" });
      if (!response.ok || !response.body) throw new Error("Event stream unavailable");
      reader = response.body.getReader();
      const decoder = new TextDecoder();
      let buffer = "";
      let type = "message";
      let data: string[] = [];
      const line = (value: string) => {
        if (value === "") {
          if (data.length) this.target.dispatchEvent(new MessageEvent(type, { data: data.join("\n"), lastEventId: this.lastEventId }));
          type = "message"; data = []; return;
        }
        if (value.startsWith(":")) return;
        const colon = value.indexOf(":");
        const field = colon < 0 ? value : value.slice(0, colon);
        let content = colon < 0 ? "" : value.slice(colon + 1);
        if (content.startsWith(" ")) content = content.slice(1);
        if (field === "event") type = content || "message";
        if (field === "data") data.push(content);
        if (field === "id" && !content.includes("\0")) this.lastEventId = content;
      };
      while (!this.stopped && !controller.signal.aborted) {
        const chunk = await reader.read();
        buffer += decoder.decode(chunk.value, { stream: !chunk.done });
        let boundary: RegExpExecArray | null;
        while ((boundary = /\r\n|\r|\n/.exec(buffer))) {
          if (!chunk.done && boundary[0] === "\r" && boundary.index === buffer.length - 1) break;
          line(buffer.slice(0, boundary.index));
          buffer = buffer.slice(boundary.index + boundary[0].length);
          if (this.stopped || controller.signal.aborted) return;
        }
        if (buffer.length > 1024 * 1024) throw new Error("Event stream line too large");
        if (chunk.done) break;
      }
      if (!this.stopped && !controller.signal.aborted) this.onerror?.(new Event("error"));
    } catch {
      if (!this.stopped && !controller.signal.aborted) this.onerror?.(new Event("error"));
    } finally {
      if (reader) { try { await reader.cancel(); } catch { /* Already closed. */ } reader.releaseLock(); }
    }
  }
}
