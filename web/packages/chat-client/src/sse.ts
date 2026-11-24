/** Incremental Server-Sent Events parser (handles chunk boundaries, multi-line data, comments). */
export interface SseMessage {
  event: string;
  data: string;
}

export class SseParser {
  private buffer = "";
  private decoder = new TextDecoder("utf-8");

  /** Feed raw bytes (multi-byte UTF-8 may be split across chunks); returns complete messages. */
  push(chunk: Uint8Array | string): SseMessage[] {
    this.buffer += typeof chunk === "string" ? chunk : this.decoder.decode(chunk, { stream: true });
    this.buffer = this.buffer.replace(/\r\n?/g, "\n");
    const out: SseMessage[] = [];
    let index: number;
    while ((index = this.buffer.indexOf("\n\n")) >= 0) {
      const block = this.buffer.slice(0, index);
      this.buffer = this.buffer.slice(index + 2);
      let event = "message";
      const data: string[] = [];
      for (const line of block.split("\n")) {
        if (!line || line.startsWith(":")) continue; // comment / keep-alive ping
        const colon = line.indexOf(":");
        const field = colon < 0 ? line : line.slice(0, colon);
        let value = colon < 0 ? "" : line.slice(colon + 1);
        if (value.startsWith(" ")) value = value.slice(1);
        if (field === "event") event = value;
        else if (field === "data") data.push(value);
      }
      if (data.length) out.push({ event, data: data.join("\n") });
    }
    return out;
  }
}
