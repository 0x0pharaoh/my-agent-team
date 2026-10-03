import { execFileSync, spawn, type ChildProcess } from "node:child_process";
import { mkdtempSync, mkdirSync, readFileSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join, resolve } from "node:path";
import { Agent } from "./agent";

export const PORT = 47396;
export const PASSPHRASE = "correct horse battery e2e";

const stateFile = join(tmpdir(), `my-team-e2e-${PORT}.json`);
const sleep = (ms: number) => new Promise((resolve) => setTimeout(resolve, ms));

export default async function setup(): Promise<() => void> {
  const dir = mkdtempSync(join(tmpdir(), "my-team-e2e-"));
  const home = join(dir, "home");
  const repo = join(dir, "repo");
  mkdirSync(repo, { recursive: true });
  // Own git root, so project_init binds this repo instead of an ancestor's (e.g. a home dotfiles repo).
  execFileSync("git", ["init", "-q", repo]);
  const serverDir = resolve(import.meta.dirname, "..", "..", "server");
  const base = `http://127.0.0.1:${PORT}`;
  const daemon = spawn(
    "uv",
    ["run", "--project", serverDir, "--python", "3.13", "python", "-m", "my_team", "serve", "--port", String(PORT)],
    { env: { ...process.env, MY_TEAM_HOME: home }, stdio: "ignore" },
  );
  try {
    const agent = await ready(base, home, daemon);
    agent.projectId = (await agent.registry("project_init", { cwd: repo, name: "E2E", key: "E2" })).project.id;
    const session = await agent.op("session_register", {
      agent_type: "opencode",
      native_id: "e2e-1",
      root_path: repo,
    });
    writeFileSync(
      stateFile,
      JSON.stringify({
        base,
        home,
        projectId: agent.projectId,
        sessionId: session.session_id,
        seat: session.agent,
        passphrase: PASSPHRASE,
      }),
    );
  } catch (error) {
    killTree(daemon);
    throw error;
  }
  return () => {
    killTree(daemon);
    rmSync(dir, { recursive: true, force: true });
    rmSync(stateFile, { force: true });
  };
}

/** Kill the daemon grandchild too: killing `uv` alone orphans `python -m my_team serve`. */
function killTree(proc: ChildProcess): void {
  if (proc.exitCode !== null || proc.pid === undefined) return;
  if (process.platform === "win32") {
    try {
      execFileSync("taskkill", ["/PID", String(proc.pid), "/T", "/F"], { stdio: "ignore" });
      return;
    } catch {
      // fall through to kill()
    }
  }
  proc.kill();
}

async function ready(base: string, home: string, daemon: ChildProcess): Promise<Agent> {
  for (let i = 0; i < 120; i++) {
    if (daemon.exitCode !== null) throw new Error("daemon exited during startup");
    try {
      const agent = Agent.fromHome(base, home);
      await agent.registry("auth_setup", { passphrase: PASSPHRASE });
      return agent;
    } catch {
      await sleep(500);
    }
  }
  throw new Error("daemon did not answer");
}

export function state(): {
  base: string;
  home: string;
  projectId: string;
  sessionId: string;
  seat: string;
  passphrase: string;
} {
  return JSON.parse(readFileSync(stateFile, "utf-8"));
}
