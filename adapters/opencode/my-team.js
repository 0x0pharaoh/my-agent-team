import { Plugin } from "@opencode/plugin";
import { execFileSync } from "node:child_process";

const PROBE = "guard-probe.txt";

function cliPath(ctx) {
  return ctx.options?.cli ?? process.env.MY_TEAM_CLI ?? "my-team";
}

function sessionContext(cli, sessionID, cwd) {
  try {
    const out = execFileSync(cli, ["hook", "session-start", "--agent", "opencode"], {
      input: JSON.stringify({ session_id: sessionID, cwd }),
      encoding: "utf-8",
      timeout: 10000,
    }).trim();
    if (!out) return null;
    if (out.startsWith("{")) return JSON.parse(out).hookSpecificOutput?.additionalContext ?? out;
    return out;
  } catch {
    return null;
  }
}

export default Plugin.define({
  id: "my-team",
  async setup(ctx) {
    const cli = cliPath(ctx);
    await ctx.session.hook("context", (event) => {
      const text = sessionContext(cli, event.sessionID, process.cwd());
      if (text) event.system.push({ type: "text", text });
    });
    await ctx.permission.hook("evaluate", (event) => {
      if (event.action === "edit" && event.resources.some((r) => r.split(/[\\/]/).pop() === PROBE))
        event.effect = "ask";
    });
  },
});
