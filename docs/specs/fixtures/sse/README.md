# Shared SSE fixtures (specs/api.md §3.3)

Recorded shapes of `POST /api/v1/chat/stream` responses. Every Channel client parses these in its
own tests: the TypeScript chat-client and Widget (`web/`) and the Kotlin client (`android/`, M4).

| File | Shows |
|---|---|
| `answer.txt` | meta → ping comment → deltas → an unknown event (must be ignored) → citations → done |
| `answer_hi.txt` | a follow-up answered in Hindi (multi-byte UTF-8) |
| `fallback.txt` | fallback text, empty citations, `finish_reason: "fallback"` |
| `error.txt` | the stream opens, then fails with an `error` event and no `done` |

The files use LF line endings and end with a blank line, as the server sends them.
