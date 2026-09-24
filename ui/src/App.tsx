import { AlertTriangle, CheckCircle2, Circle, CircleDashed, Clock, LogOut, Pause, Radio } from "lucide-react";
import { type FormEvent, type ReactNode, useCallback, useEffect, useRef, useState } from "react";
import { ApiError, call, follow, op, session } from "./api";

type Criterion = { text: string; done: boolean };
type Ticket = {
  id: string; key: string; title: string; description: string; status: string; priority: string; version: number;
  assignee: { id: string; name: string } | null;
  holder: { session_id: string; name: string; last_heartbeat_ms: number | null } | null;
  acceptance_criteria: Criterion[]; pending_pickup: boolean; stalled: boolean; waiting_on: string[];
  changes_requested: boolean; status_reason: string | null; implementation_summary: string | null;
  human_actions: string[];
};
type Session = { id: string; status: string; ticket: string | null; last_heartbeat_ms: number | null; last_activity_ms: number };
type Agent = { id: string; display_name: string; agent_type: string; role: string | null; sessions: Session[] };
type Project = { id: string; name: string; key: string; roots: string[] };
type Board = { tickets: Ticket[]; agents: Agent[] };

const COLUMNS = [
  ["proposed", "Proposed"], ["backlog", "Backlog"], ["ready", "Ready"], ["in_progress", "In progress"],
  ["in_review", "In review"], ["blocked", "Blocked"], ["done", "Done"],
] as const;
type Tab = "board" | "inbox" | "memory" | "agents";
const TABS: [Tab, string][] = [["board", "Board"], ["inbox", "Inbox"], ["memory", "Memory"], ["agents", "Agents"]];
const ACTION_LABELS: Record<string, string> = {
  accept: "Accept", ready: "Mark ready", done: "Close as done", request_changes: "Request changes",
  pause: "Pause", unblock: "Unblock", cancel: "Cancel",
};

function useNow(intervalMs = 5000) {
  const [now, setNow] = useState(Date.now());
  useEffect(() => {
    const timer = setInterval(() => setNow(Date.now()), intervalMs);
    return () => clearInterval(timer);
  }, [intervalMs]);
  return now;
}

function ago(ms: number | null, now: number) {
  if (!ms) return "never reported";
  const seconds = Math.max(0, Math.round((now - ms) / 1000));
  if (seconds < 60) return `${seconds}s ago`;
  if (seconds < 3600) return `${Math.round(seconds / 60)}m ago`;
  return `${Math.round(seconds / 3600)}h ago`;
}

function Chip({ tone, icon, children }: { tone: string; icon: ReactNode; children: ReactNode }) {
  return (
    <span className={`inline-flex items-center gap-1 rounded-sm border border-border px-1.5 py-0.5 text-xs ${tone}`}>
      {icon}
      {children}
    </span>
  );
}

const button = "rounded-md px-3 py-1.5 text-sm font-medium disabled:opacity-50";
const primary = `${button} bg-primary text-bg hover:bg-primary-hover`;
const secondary = `${button} border border-border hover:bg-surface-raised`;
const field = "w-full rounded-sm border border-border bg-bg px-2 py-1.5 text-sm";

function Login({ setupRequired, onDone }: { setupRequired: boolean; onDone: () => void }) {
  const [passphrase, setPassphrase] = useState("");
  const [error, setError] = useState("");
  async function submit(event: FormEvent) {
    event.preventDefault();
    try {
      await call("/auth/login", { passphrase });
      onDone();
    } catch (exc) {
      setError(exc instanceof ApiError ? exc.message : "Login failed.");
    }
  }
  return (
    <main className="mx-auto mt-24 max-w-sm px-4">
      <h1 className="mb-4 text-xl font-semibold">my-team</h1>
      {setupRequired ? (
        <p className="text-muted">
          Set a passphrase first: run <code className="font-mono">my-team setup</code> in a terminal, then reload.
        </p>
      ) : (
        <form onSubmit={submit} className="space-y-3">
          <label className="block text-sm">
            Passphrase
            <input type="password" autoFocus className={`${field} mt-1`} value={passphrase}
              onChange={(e) => setPassphrase(e.target.value)} aria-describedby="login-error" />
          </label>
          {error && <p id="login-error" className="text-sm text-danger">{error}</p>}
          <button type="submit" className={primary}>Log in</button>
        </form>
      )}
    </main>
  );
}

function TicketCard({ ticket, agents, run, open }: {
  ticket: Ticket; agents: Agent[]; run: (name: string, body: object) => void; open: () => void;
}) {
  const now = useNow();
  return (
    <li className="rounded-md border border-border bg-surface-raised p-2 shadow-card">
      <button className="block w-full text-left" onClick={open}>
        <span className="font-mono text-xs text-muted">{ticket.key}</span>
        <span className="block font-medium">{ticket.title}</span>
      </button>
      <div className="mt-1 flex flex-wrap gap-1">
        {ticket.pending_pickup && <Chip tone="text-info" icon={<CircleDashed size={12} />}>Pending pickup</Chip>}
        {ticket.holder && (
          <Chip tone="text-primary" icon={<Radio size={12} />}>
            {ticket.holder.name} · {ago(ticket.holder.last_heartbeat_ms, now)}
          </Chip>
        )}
        {ticket.stalled && <Chip tone="text-danger" icon={<AlertTriangle size={12} />}>Stalled</Chip>}
        {ticket.waiting_on.length > 0 && (
          <Chip tone="text-danger" icon={<Clock size={12} />}>Waiting on {ticket.waiting_on.join(", ")}</Chip>
        )}
        {ticket.changes_requested && <Chip tone="text-warning" icon={<Pause size={12} />}>Changes requested</Chip>}
      </div>
      <div className="mt-2 flex gap-1">
        <select aria-label={`Assign ${ticket.key}`} className={`${field} text-xs`} value={ticket.assignee?.id ?? ""}
          disabled={ticket.status === "done" || ticket.status === "cancelled"}
          onChange={(e) => run("ticket_assign", { key: ticket.key, agent_id: e.target.value || null, version: ticket.version })}>
          <option value="">Unassigned</option>
          {agents.map((agent) => <option key={agent.id} value={agent.id}>{agent.display_name}</option>)}
        </select>
        {ticket.human_actions.length > 0 && (
          <select aria-label={`Change status of ${ticket.key}`} className={`${field} text-xs`} value=""
            onChange={(e) => e.target.value && run("ticket_transition", { key: ticket.key, action: e.target.value, version: ticket.version })}>
            <option value="">Status…</option>
            {ticket.human_actions.map((action) => <option key={action} value={action}>{ACTION_LABELS[action]}</option>)}
          </select>
        )}
      </div>
    </li>
  );
}

type HistoryEvent = { id: number; type: string; actor_type: string; created_ms: number; payload: Record<string, unknown> };

function TicketDialog({ project, ticket, onClose, run }: {
  project: string; ticket: Ticket; onClose: () => void; run: (name: string, body: object) => void;
}) {
  const ref = useRef<HTMLDialogElement>(null);
  const [criteria, setCriteria] = useState(ticket.acceptance_criteria.map((c) => c.text).join("\n"));
  const [history, setHistory] = useState<HistoryEvent[]>([]);
  const now = useNow();
  useEffect(() => {
    ref.current?.showModal();
    op<{ events: HistoryEvent[] }>(project, "ticket_history", { key: ticket.key }).then((r) => setHistory(r.data.events));
  }, [project, ticket.key, ticket.version]);
  return (
    <dialog ref={ref} onClose={onClose}
      className="m-auto w-full max-w-2xl rounded-lg border border-border bg-surface p-4 text-text shadow-overlay">
      <header className="mb-3 flex items-start justify-between gap-4">
        <div>
          <span className="font-mono text-xs text-muted">{ticket.key} · {ticket.status.replace("_", " ")}</span>
          <h2 className="text-lg font-semibold">{ticket.title}</h2>
        </div>
        <button className={secondary} onClick={() => ref.current?.close()}>Close</button>
      </header>
      {ticket.description && <p className="mb-3 whitespace-pre-wrap">{ticket.description}</p>}
      {ticket.implementation_summary && (
        <section className="mb-3"><h3 className="font-medium">Implementation summary</h3>
          <p className="whitespace-pre-wrap text-muted">{ticket.implementation_summary}</p></section>
      )}
      <label className="mb-3 block text-sm font-medium">
        Acceptance criteria (one per line)
        <textarea className={`${field} mt-1 h-24 font-normal`} value={criteria} onChange={(e) => setCriteria(e.target.value)} />
      </label>
      <button className={secondary} onClick={() => run("ticket_edit", {
        key: ticket.key, version: ticket.version,
        acceptance_criteria: criteria.split("\n").map((text) => text.trim()).filter(Boolean),
      })}>Save criteria</button>
      <h3 className="mt-4 font-medium">History</h3>
      <ol className="mt-1 max-h-48 space-y-1 overflow-y-auto text-xs text-muted">
        {history.map((event) => (
          <li key={event.id}>
            {ago(event.created_ms, now)} · {event.type} by {event.actor_type}
            {typeof event.payload.note === "string" && <> — {event.payload.note}</>}
          </li>
        ))}
      </ol>
    </dialog>
  );
}

function BoardView({ project, board, run }: { project: string; board: Board; run: (name: string, body: object) => void }) {
  const [title, setTitle] = useState("");
  const [criteria, setCriteria] = useState("");
  const [openKey, setOpenKey] = useState<string | null>(null);
  const open = board.tickets.find((ticket) => ticket.key === openKey);
  function create(event: FormEvent) {
    event.preventDefault();
    const lines = criteria.split("\n").map((line) => line.trim()).filter(Boolean);
    run("ticket_create", { title, acceptance_criteria: lines, status: lines.length ? "ready" : "backlog" });
    setTitle("");
    setCriteria("");
  }
  return (
    <>
      <form onSubmit={create} className="mb-4 flex flex-wrap items-start gap-2">
        <input required aria-label="New ticket title" placeholder="New ticket title" className={`${field} max-w-sm`}
          value={title} onChange={(e) => setTitle(e.target.value)} />
        <textarea aria-label="Acceptance criteria, one per line" placeholder="Acceptance criteria, one per line (makes it ready)"
          className={`${field} h-9 max-w-sm`} value={criteria} onChange={(e) => setCriteria(e.target.value)} />
        <button type="submit" className={primary}>Create ticket</button>
      </form>
      <div className="flex gap-3 overflow-x-auto pb-4">
        {COLUMNS.map(([status, label]) => {
          const tickets = board.tickets.filter((ticket) => ticket.status === status);
          return (
            <section key={status} className="w-64 shrink-0 rounded-lg bg-surface p-2" aria-label={label}>
              <h2 className="mb-2 text-sm font-semibold">{label} <span className="text-muted">{tickets.length}</span></h2>
              <ul className="space-y-2">
                {tickets.map((ticket) => (
                  <TicketCard key={ticket.id} ticket={ticket} agents={board.agents} run={run} open={() => setOpenKey(ticket.key)} />
                ))}
              </ul>
              {tickets.length === 0 && <p className="px-1 text-xs text-muted">Nothing here.</p>}
            </section>
          );
        })}
      </div>
      {open && <TicketDialog project={project} ticket={open} onClose={() => setOpenKey(null)} run={run} />}
    </>
  );
}

const SESSION_TONE: Record<string, [string, ReactNode]> = {
  active: ["text-success", <CheckCircle2 size={12} />],
  idle: ["text-muted", <Circle size={12} />],
  offline: ["text-muted", <CircleDashed size={12} />],
  unknown: ["text-warning", <AlertTriangle size={12} />],
};

function AgentsView({ agents, run }: { agents: Agent[]; run: (name: string, body: object) => void }) {
  const now = useNow();
  if (agents.length === 0) {
    return <p className="text-muted">No agent has connected yet. Start Claude Code in the project and send a prompt.</p>;
  }
  return (
    <table className="w-full text-left text-sm">
      <thead className="text-muted"><tr><th className="py-1">Seat</th><th>Type</th><th>Role</th><th>Sessions</th></tr></thead>
      <tbody>
        {agents.map((agent) => (
          <tr key={agent.id} className="border-t border-border align-top">
            <td className="py-2">
              <input aria-label={`Name of ${agent.display_name}`} className={`${field} max-w-40`} defaultValue={agent.display_name}
                onBlur={(e) => e.target.value !== agent.display_name && run("agent_update", { agent_id: agent.id, display_name: e.target.value })} />
            </td>
            <td className="py-2 font-mono text-xs">{agent.agent_type}</td>
            <td className="py-2">
              <input aria-label={`Role of ${agent.display_name}`} className={`${field} max-w-48`} defaultValue={agent.role ?? ""}
                onBlur={(e) => e.target.value !== (agent.role ?? "") && run("agent_update", { agent_id: agent.id, role: e.target.value })} />
            </td>
            <td className="space-y-1 py-2">
              {agent.sessions.length === 0 && <span className="text-muted">No sessions in the last day</span>}
              {agent.sessions.map((s) => (
                <div key={s.id}>
                  <Chip tone={SESSION_TONE[s.status]?.[0] ?? "text-muted"} icon={SESSION_TONE[s.status]?.[1]}>
                    {s.status} · reported {ago(s.last_heartbeat_ms, now)}
                  </Chip>
                  {s.ticket && <span className="ml-2 font-mono text-xs">{s.ticket}</span>}
                </div>
              ))}
            </td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}

type Question = { id: string; kind: string; prompt: string; options: string[]; recommendation: string | null;
  answer: string | null; status: string; ticket: string | null; asked_by: string | null; created_ms: number };
type Message = { id: string; channel: string; ticket: string | null; from: string; trust: string; body: string;
  requires_response: boolean; created_ms: number; recipients: { to: string; read_ms: number | null }[] };
type Memory = { id: string; kind: string; title: string; body: string; source: string; status: string;
  author: string; ticket: string | null; stale: string[]; created_ms: number };

function QuestionCard({ question, run }: { question: Question; run: (name: string, body: object) => void }) {
  const [answer, setAnswer] = useState(question.recommendation ?? "");
  return (
    <li className="rounded-md border border-border bg-surface-raised p-3">
      <p className="text-xs text-muted">
        {question.kind} from {question.asked_by ?? "an agent"}{question.ticket && ` · ${question.ticket}`}
      </p>
      <p className="my-1 whitespace-pre-wrap font-medium">{question.prompt}</p>
      {question.options.length > 0 && (
        <div className="mb-2 flex flex-wrap gap-1">
          {question.options.map((option) => (
            <button key={option} className={secondary} onClick={() => setAnswer(option)}>{option}</button>
          ))}
        </div>
      )}
      <div className="flex gap-2">
        <input aria-label="Answer" className={field} value={answer} onChange={(e) => setAnswer(e.target.value)} />
        <button className={primary} disabled={!answer.trim()}
          onClick={() => run("question_answer", { id: question.id, answer })}>Answer</button>
        <button className={secondary}
          onClick={() => run("question_answer", { id: question.id, answer: answer || "No", reject: true })}>Reject</button>
      </div>
    </li>
  );
}

function InboxView({ project, agents, tick, run }: {
  project: string; agents: Agent[]; tick: number; run: (name: string, body: object) => void;
}) {
  const [questions, setQuestions] = useState<Question[]>([]);
  const [messages, setMessages] = useState<Message[]>([]);
  const [to, setTo] = useState("all");
  const [body, setBody] = useState("");
  const now = useNow();
  useEffect(() => {
    op<{ questions: Question[] }>(project, "questions_list", { status: "open" }).then((r) => setQuestions(r.data.questions));
    op<{ messages: Message[] }>(project, "messages_recent").then((r) => setMessages(r.data.messages));
  }, [project, tick]);
  function send(event: FormEvent) {
    event.preventDefault();
    run("message_send", { to, body });
    setBody("");
  }
  return (
    <div className="grid gap-6 lg:grid-cols-2">
      <section aria-label="Questions">
        <h2 className="mb-2 font-semibold">Questions waiting on you <span className="text-muted">{questions.length}</span></h2>
        {questions.length === 0 && <p className="text-sm text-muted">No open questions.</p>}
        <ul className="space-y-2">{questions.map((q) => <QuestionCard key={q.id} question={q} run={run} />)}</ul>
      </section>
      <section aria-label="Messages">
        <h2 className="mb-2 font-semibold">Messages</h2>
        <form onSubmit={send} className="mb-3 flex gap-2">
          <select aria-label="Send to" className={`${field} w-40`} value={to} onChange={(e) => setTo(e.target.value)}>
            <option value="all">Everyone</option>
            {agents.map((agent) => <option key={agent.id} value={agent.display_name}>{agent.display_name}</option>)}
          </select>
          <input required aria-label="Message" placeholder="Message" className={field} value={body}
            onChange={(e) => setBody(e.target.value)} />
          <button type="submit" className={primary}>Send</button>
        </form>
        {messages.length === 0 && <p className="text-sm text-muted">No messages yet.</p>}
        <ul className="space-y-2">
          {messages.map((m) => {
            const mine = m.recipients.find((r) => r.to === "human");
            const unread = Boolean(mine && !mine.read_ms);
            return (
              <li key={m.id} className={`rounded-md border p-2 ${unread ? "border-primary" : "border-border"}`}>
                <p className="text-xs text-muted">
                  {m.from} → {m.recipients.map((r) => `${r.to}${r.read_ms ? " ✓" : ""}`).join(", ")}
                  {m.ticket && ` · ${m.ticket}`} · {ago(m.created_ms, now)}{m.requires_response && " · needs reply"}
                </p>
                <p className="whitespace-pre-wrap">{m.body}</p>
                {unread && (
                  <button className={`${secondary} mt-1 text-xs`} onClick={() => run("inbox", { ack: [m.id] })}>Mark read</button>
                )}
              </li>
            );
          })}
        </ul>
      </section>
    </div>
  );
}

function MemoryView({ project, tick, run }: { project: string; tick: number; run: (name: string, body: object) => void }) {
  const [q, setQ] = useState("");
  const [items, setItems] = useState<Memory[]>([]);
  const [title, setTitle] = useState("");
  const [body, setBody] = useState("");
  useEffect(() => {
    const timer = setTimeout(() => {
      op<{ memories: Memory[] }>(project, "memory_search", { q: q || null, limit: 50 }).then((r) => setItems(r.data.memories));
    }, 200);
    return () => clearTimeout(timer);
  }, [project, q, tick]);
  function instruct(event: FormEvent) {
    event.preventDefault();
    run("memory_write", { kind: "human_instruction", title, body });
    setTitle("");
    setBody("");
  }
  return (
    <div className="grid gap-6 lg:grid-cols-[2fr_1fr]">
      <section aria-label="Memory">
        <input aria-label="Search memory" placeholder="Search memory…" className={`${field} mb-3`} value={q}
          onChange={(e) => setQ(e.target.value)} />
        {items.length === 0 && <p className="text-sm text-muted">Nothing remembered yet.</p>}
        <ul className="space-y-2">
          {items.map((m) => (
            <li key={m.id} className="rounded-md border border-border p-2">
              <div className="flex flex-wrap items-center gap-1 text-xs text-muted">
                <span className="font-mono">{m.kind}</span> · {m.author}{m.ticket && ` · ${m.ticket}`}
                {m.source === "human_confirmed" && <Chip tone="text-success" icon={<CheckCircle2 size={12} />}>Confirmed</Chip>}
                {m.status === "needs_review" && <Chip tone="text-warning" icon={<AlertTriangle size={12} />}>Needs review</Chip>}
                {m.stale.length > 0 && <Chip tone="text-warning" icon={<Clock size={12} />}>Outdated? {m.stale.join(", ")}</Chip>}
              </div>
              <p className="font-medium">{m.title}</p>
              {m.body && <p className="whitespace-pre-wrap text-sm">{m.body}</p>}
              <div className="mt-1 flex gap-1">
                {m.source !== "human_confirmed" && (
                  <button className={`${secondary} text-xs`}
                    onClick={() => run("memory_correct", { id: m.id, action: "confirm" })}>Confirm</button>
                )}
                <button className={`${secondary} text-xs`}
                  onClick={() => run("memory_correct", { id: m.id, action: "retract" })}>Retract</button>
              </div>
            </li>
          ))}
        </ul>
      </section>
      <form onSubmit={instruct} className="space-y-2" aria-label="Add a human instruction">
        <h2 className="font-semibold">Add an instruction for every agent</h2>
        <input required aria-label="Instruction" placeholder="e.g. Never modify billing/" className={field} value={title}
          onChange={(e) => setTitle(e.target.value)} />
        <textarea aria-label="Details" placeholder="Details (optional)" className={`${field} h-24`} value={body}
          onChange={(e) => setBody(e.target.value)} />
        <button type="submit" className={primary}>Save instruction</button>
      </form>
    </div>
  );
}

function Dashboard({ onLogout }: { onLogout: () => void }) {
  const [projects, setProjects] = useState<Project[]>([]);
  const [projectId, setProjectId] = useState<string | null>(null);
  const [board, setBoard] = useState<Board | null>(null);
  const [cursor, setCursor] = useState<string | null>(null);
  const [tab, setTab] = useState<Tab>("board");
  const [tick, setTick] = useState(0);
  const [live, setLive] = useState(false);
  const [error, setError] = useState("");

  useEffect(() => {
    call<{ projects: Project[] }>("/api/v1/registry/projects_list").then((r) => {
      setProjects(r.data.projects);
      setProjectId((current) => current ?? r.data.projects[0]?.id ?? null);
    });
  }, []);

  const reload = useCallback(async () => {
    if (!projectId) return;
    const result = await op<Board>(projectId, "board");
    setBoard(result.data);
    setTick((value) => value + 1);
    setCursor((current) => current ?? result.cursor ?? null);
  }, [projectId]);

  useEffect(() => {
    setBoard(null);
    setCursor(null);
    reload();
  }, [reload]);

  useEffect(() => {
    if (!projectId || !cursor) return;
    let pending: ReturnType<typeof setTimeout> | undefined;
    const stop = follow(projectId, cursor, () => {
      clearTimeout(pending);
      pending = setTimeout(reload, 250);
    }, setLive);
    return () => { stop(); clearTimeout(pending); };
  }, [projectId, cursor, reload]);

  const run = useCallback(async (name: string, body: object) => {
    if (!projectId) return;
    setError("");
    try {
      await op(projectId, name, body);
    } catch (exc) {
      setError(exc instanceof ApiError ? exc.message : "Request failed.");
    }
    reload();
  }, [projectId, reload]);

  async function logout() {
    await call("/auth/logout");
    onLogout();
  }

  return (
    <div className="px-4 py-3">
      <header className="mb-4 flex flex-wrap items-center gap-3 border-b border-border pb-3">
        <span className="font-semibold">my-team</span>
        <select aria-label="Project" className={`${field} w-auto`} value={projectId ?? ""} onChange={(e) => setProjectId(e.target.value)}>
          {projects.map((project) => <option key={project.id} value={project.id}>{project.name} ({project.key})</option>)}
        </select>
        <nav className="flex gap-1" aria-label="Views">
          {TABS.map(([name, label]) => (
            <button key={name} aria-current={tab === name ? "page" : undefined}
              className={`${button} ${tab === name ? "bg-primary-subtle text-primary" : "hover:bg-surface"}`}
              onClick={() => setTab(name)}>{label}</button>
          ))}
        </nav>
        <span className="ml-auto flex items-center gap-2 text-xs text-muted" role="status">
          <span className={`h-2 w-2 rounded-full ${live ? "bg-success" : "bg-warning"}`} />
          {live ? "Live" : "Reconnecting…"}
        </span>
        <button className={secondary} onClick={logout} aria-label="Log out"><LogOut size={16} /></button>
      </header>
      {error && <p role="alert" className="mb-3 text-sm text-danger">{error}</p>}
      {projects.length === 0 && (
        <p className="text-muted">No projects yet. In an agent session, run <code className="font-mono">/my-team:init</code>.</p>
      )}
      {board && projectId && tab === "board" && <BoardView project={projectId} board={board} run={run} />}
      {board && projectId && tab === "inbox" && <InboxView project={projectId} agents={board.agents} tick={tick} run={run} />}
      {board && projectId && tab === "memory" && <MemoryView project={projectId} tick={tick} run={run} />}
      {board && projectId && tab === "agents" && <AgentsView agents={board.agents} run={run} />}
    </div>
  );
}

export default function App() {
  const [auth, setAuth] = useState<{ authenticated: boolean; setup_required: boolean } | null>(null);
  const check = useCallback(() => { session().then(setAuth); }, []);
  useEffect(check, [check]);
  if (!auth) return null;
  if (!auth.authenticated) return <Login setupRequired={auth.setup_required} onDone={check} />;
  return <Dashboard onLogout={check} />;
}

