import { expect, test } from "vitest";
import { frames } from "./api";

test("frames splits SSE blocks, skips comments, and joins data across chunks", async () => {
  const chunks = ["retry: 2000\n\n: ping\n\nid: e1.4\nevent: change\nda", 'ta: {"a":1}\n\nevent: reset\ndata: {}\n\n'];
  const body = new ReadableStream<Uint8Array>({
    start(controller) {
      for (const chunk of chunks) controller.enqueue(new TextEncoder().encode(chunk));
      controller.close();
    },
  });
  const seen = [];
  for await (const frame of frames(body)) seen.push(frame);
  expect(seen).toEqual([
    { id: "e1.4", event: "change", data: '{"a":1}' },
    { event: "reset", data: "{}" },
  ]);
});
