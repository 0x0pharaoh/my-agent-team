import { execFile } from "node:child_process";
import path from "node:path";

const EDIT_ACTIONS = new Set(["edit", "write", "patch", "apply_patch"]);

const dirs = new Map();
const texts = new Map();

function spawn(cli, args, input) {
  return new Promise((resolve) => {
    const child = execFile(cli, args, { timeout: 10000 }, (error, stdout) => {
      resolve(error ? "" : String(stdout).trim());
    });
    child.stdin.end(input);
  });
}

async function cwdFor(ctx, base, sessionID) {
  const known = dirs.get(sessionID);
  if (known !== undefined) return known;
  let cwd = base;
  try {
    const info = await ctx.session.get({ sessionID });
    if (info && info.location && info.location.directory) cwd = info.location.directory;
  } catch {
    // without the session directory the hook cannot resolve the project
  }
  if (cwd) dirs.set(sessionID, cwd);
  return cwd;
}

async function contextText(ctx, cli, base, sessionID) {
  const cwd = await cwdFor(ctx, base, sessionID);
  if (!cwd) return "";
  return spawn(cli, ["hook", "session-start", "--agent", "opencode"],
    JSON.stringify({ session_id: sessionID, cwd }));
}

function touched(event) {
  const files = new Set();
  for (const resource of event.resources || []) {
    if (typeof resource === "string") files.add(resource);
  }
  for (const entry of ((event.metadata || {}).files || [])) {
    if (entry && typeof entry.file === "string") files.add(entry.file);
  }
  return [...files];
}

export default {
  id: "my-team",
  async setup(ctx) {
    const cli = (ctx.options && ctx.options.cli) || "__MY_TEAM_CLI__";
    const base = (ctx.location && ctx.location.directory) || "";
    await ctx.session.hook("prompt", async (event) => {
      try {
        if (!event || !event.sessionID) return;
        texts.set(event.sessionID, await contextText(ctx, cli, base, event.sessionID));
      } catch {
        // fail open: the context hook pushes nothing without cached text
      }
    });
    await ctx.session.hook("context", async (event) => {
      try {
        if (!event || !event.sessionID || !Array.isArray(event.system)) return;
        let text = texts.get(event.sessionID);
        if (!texts.has(event.sessionID)) {
          text = await contextText(ctx, cli, base, event.sessionID);
          texts.set(event.sessionID, text);
        }
        if (text) event.system.push({ type: "text", text });
      } catch {
        // fail open
      }
    });
    await ctx.permission.hook("evaluate", async (event) => {
      try {
        if (!event || !EDIT_ACTIONS.has(event.action) || event.effect !== "allow") return;
        const cwd = await cwdFor(ctx, base, event.sessionID);
        if (!cwd) return;
        for (const file of touched(event)) {
          const target = path.isAbsolute(file) ? file : path.join(cwd, file);
          const reason = await spawn(cli, ["hook", "pre-edit", "--agent", "opencode"],
            JSON.stringify({ session_id: event.sessionID, cwd, file_path: target }));
          if (reason) {
            event.effect = "ask";
            return;
          }
        }
      } catch {
        // fail open: leave the decision unchanged
      }
    });
  },
};
