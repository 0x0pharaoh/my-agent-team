import { createHash, createHmac, randomBytes } from "node:crypto";
import { readFileSync } from "node:fs";
import { join } from "node:path";

/** Minimal signed agent client mirroring server/tests/conftest.py Agent. */
export class Agent {
  projectId = "";
  sessionId = "";

  constructor(private base: string, private secret: Buffer) {}

  static fromHome(base: string, home: string): Agent {
    return new Agent(base, readFileSync(join(home, "data", "secret")));
  }

  private headers(path: string, body: Buffer, session?: string): Record<string, string> {
    const ts = String(Date.now());
    const nonce = randomBytes(16).toString("hex");
    const digest = createHash("sha256").update(body).digest("hex");
    const sig = createHmac("sha256", this.secret).update(`POST\n${path}\n${ts}\n${nonce}\n${digest}`).digest("hex");
    const headers: Record<string, string> = {
      "Content-Type": "application/json",
      "X-My-Team": "1",
      "x-my-team-ts": ts,
      "x-my-team-nonce": nonce,
      "x-my-team-sig": sig,
    };
    if (session) headers["x-my-team-session"] = session;
    return headers;
  }

  async call(path: string, payload: object, session?: string): Promise<Record<string, any>> {
    const body = Buffer.from(JSON.stringify(payload));
    const response = await fetch(this.base + path, {
      method: "POST",
      headers: this.headers(path, body, session),
      body,
    });
    const envelope = await response.json();
    if (!envelope.success) throw new Error(`${path}: ${JSON.stringify(envelope.error)}`);
    return envelope.data;
  }

  registry(name: string, payload: object): Promise<Record<string, any>> {
    return this.call(`/api/v1/registry/${name}`, payload);
  }

  op(name: string, payload: object, session?: string): Promise<Record<string, any>> {
    return this.call(`/api/v1/projects/${this.projectId}/${name}`, payload, session ?? this.sessionId);
  }
}
