import { describe, expect, it } from "vitest";
import { buildQuery } from "../conversations/ConversationsPage";
import { parseOrigins, widgetSnippet } from "./ChannelsPage";

describe("Channels page helpers", () => {
  it("parses and validates Allowed Origins like the server", () => {
    expect(parseOrigins("https://Shop.example.com/\nhttps://shop.example.com, http://localhost:8080")).toEqual({
      origins: ["https://shop.example.com", "http://localhost:8080"],
      invalid: [],
    });
    expect(parseOrigins("* shop.example.com https://x.com/path javascript:alert(1)").invalid).toEqual([
      "*",
      "shop.example.com",
      "https://x.com/path",
      "javascript:alert(1)",
    ]);
  });

  it("builds the Widget snippets with key and API filled in", () => {
    const s = widgetSnippet("pk_abc", "https://chat.example.com");
    expect(s).toContain('src="https://chat.example.com/widget/v1/widget.js"');
    expect(s).toContain('data-key="pk_abc"');
    expect(s).toContain('data-mode="launcher"');
    expect(widgetSnippet("pk_abc", "https://chat.example.com", "inline")).toContain('<codura-chat key="pk_abc"');
  });

  it("builds the log viewer query from filters", () => {
    expect(buildQuery({ assistant_id: "asst_1", channel: "", feedback: "down" }, "cur")).toBe(
      "/api/admin/conversations?assistant_id=asst_1&feedback=down&cursor=cur&limit=25",
    );
  });
});
