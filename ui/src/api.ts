export class ApiError extends Error {
  constructor(public code: string, message: string) {
    super(message);
  }
}

const HEADERS = { "Content-Type": "application/json", "X-My-Team": "1" };

export async function call<T>(path: string, body: unknown = {}): Promise<{ data: T; cursor?: string }> {
  const response = await fetch(path, { method: "POST", headers: HEADERS, body: JSON.stringify(body) });
  const envelope = await response.json();
  if (!envelope.success) throw new ApiError(envelope.error.code, envelope.error.message);
  return { data: envelope.data, cursor: envelope.meta?.cursor };
}

export const op = <T>(project: string, name: string, body: unknown = {}) =>
  call<T>(`/api/v1/projects/${project}/${name}`, body);

export async function session(): Promise<{ authenticated: boolean; setup_required: boolean }> {
  const response = await fetch("/auth/me", { headers: HEADERS });
  return (await response.json()).data;
}

export type Frame = { id?: string; event?: string; data?: string };

export async function* frames(body: ReadableStream<Uint8Array>): AsyncGenerator<Frame> {
  const reader = body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  while (true) {
    const { value, done } = await reader.read();
    if (done) return;
    buffer += decoder.decode(value, { stream: true });
    let end: number;
    while ((end = buffer.indexOf("\n\n")) >= 0) {
      const frame: Frame = {};
      for (const line of buffer.slice(0, end).split("\n")) {
        const split = line.indexOf(":");
        if (split <= 0) continue;
        const field = line.slice(0, split);
        const value = line.slice(split + 1).replace(/^ /, "");
        if (field === "id" || field === "event") frame[field] = value;
        if (field === "data") frame.data = (frame.data ?? "") + value;
      }
      buffer = buffer.slice(end + 2);
      if (frame.event) yield frame;
    }
  }
}

export function follow(project: string, cursor: string, onChange: () => void, onLive: (live: boolean) => void) {
  const controller = new AbortController();
  let position = cursor;
  (async () => {
    while (!controller.signal.aborted) {
      try {
        const response = await fetch(`/api/v1/events/stream?cursors=${project}:${position}`, {
          headers: HEADERS,
          signal: controller.signal,
        });
        if (!response.ok || !response.body) throw new Error(String(response.status));
        onLive(true);
        for await (const frame of frames(response.body)) {
          if (frame.id) position = frame.id;
          onChange();
        }
      } catch {
        if (controller.signal.aborted) return;
      }
      onLive(false);
      await new Promise((resolve) => setTimeout(resolve, 2000));
    }
  })();
  return () => controller.abort();
}
