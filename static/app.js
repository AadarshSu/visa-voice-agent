import { Conversation } from "https://cdn.jsdelivr.net/npm/@elevenlabs/client@1.25.0/+esm";

const talk = document.getElementById("talk");
const statusLine = document.getElementById("status");
const progress = document.getElementById("progress");
const steps = document.getElementById("steps");
const planBox = document.getElementById("plan");
const transcript = document.getElementById("transcript");

let conversation = null;
let job = null; // { id, seen }

const clientTools = {
  async start_visa_research(params) {
    const response = await fetch("/api/research", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(params),
    });
    if (!response.ok) return `Could not start research: ${await response.text()}`;
    job = { id: (await response.json()).job_id, seen: 0 };
    steps.replaceChildren();
    planBox.hidden = true;
    progress.hidden = false;
    return "Research started. It usually takes a minute or two. Call check_visa_research now.";
  },

  async check_visa_research() {
    if (!job) return "No research is running. Start one with start_visa_research.";
    const response = await fetch(`/api/research/${job.id}?seen=${job.seen}`);
    const result = await response.json();
    job.seen = result.stages_seen;
    for (const stage of result.new_stages) steps.append(item(stage));
    if (result.status === "done") showPlan(result.plan);
    // The full plan stays on screen; the agent gets only the brief.
    const { plan, ...forAgent } = result;
    return JSON.stringify(forAgent);
  },
};

talk.addEventListener("click", async () => {
  if (conversation) {
    await conversation.endSession();
    return;
  }
  try {
    talk.disabled = true;
    statusLine.textContent = "Connecting…";
    await navigator.mediaDevices.getUserMedia({ audio: true });
    const { agent_id } = await (await fetch("/api/config")).json();
    conversation = await Conversation.startSession({
      agentId: agent_id,
      connectionType: "webrtc",
      clientTools,
      onConnect: () => setLive(true),
      onDisconnect: () => {
        conversation = null;
        setLive(false);
      },
      onError: (message) => (statusLine.textContent = `Error: ${message}`),
      onModeChange: ({ mode }) => {
        if (conversation) statusLine.textContent = mode === "speaking" ? "Speaking" : "Listening";
      },
      onMessage: ({ source, message }) => addLine(source === "user" ? "You" : "Agent", message),
    });
  } catch (error) {
    statusLine.textContent = `Could not start: ${error.message}`;
  } finally {
    talk.disabled = false;
  }
});

function setLive(live) {
  talk.dataset.live = String(live);
  talk.textContent = live ? "End conversation" : "Start talking";
  statusLine.textContent = live ? "Listening" : "Not connected";
}

function addLine(who, text) {
  const li = document.createElement("li");
  const name = document.createElement("span");
  name.className = "who";
  name.textContent = `${who}:`;
  li.append(name, text);
  transcript.append(li);
  li.scrollIntoView({ block: "nearest" });
}

function item(text, className) {
  const li = document.createElement("li");
  li.textContent = text;
  if (className) li.className = className;
  return li;
}

function el(tag, text, className) {
  const node = document.createElement(tag);
  if (text) node.textContent = text;
  if (className) node.className = className;
  return node;
}

function showPlan(plan) {
  const decision = { true: "Visa required", false: "No visa required" }[plan.visa_required]
    ?? "Not settled by the official pages";
  const parts = [el("p", decision, "decision")];
  if (plan.visa_type) parts.push(el("p", plan.visa_type, "small"));
  if (plan.decision_condition) parts.push(el("p", `Only if: ${plan.decision_condition}`, "warn"));
  parts.push(el("p", plan.explanation));

  const list = (title, entries, render, ordered = false) => {
    if (!entries?.length) return;
    parts.push(el("h3", title));
    const container = el(ordered ? "ol" : "ul");
    for (const entry of entries) container.append(item(render(entry)));
    parts.push(container);
  };
  list("Could not verify", plan.gaps, (g) => `${g.missing} ${g.next_step}`);
  list("Sources disagree", plan.disagreements, (d) => `${d.topic}: ${d.summary}`);
  list("Exceptions", plan.exceptions, (e) => `If ${e.condition}: ${e.consequence}`);
  list("Steps", plan.application_steps, (s) => `${s.title}: ${s.action} (${s.timing})`, true);
  list("Requirements", plan.requirements, (r) => `${r.name}: ${r.description}`);

  if (plan.sources?.length) {
    parts.push(el("h3", "Sources"));
    const sources = el("ul");
    for (const s of plan.sources) {
      const li = document.createElement("li");
      const link = el("a", s.title);
      link.href = s.url;
      link.target = "_blank";
      link.rel = "noopener";
      li.append(link, el("span", ` · ${s.authority}${s.is_stale ? " · cached copy" : ""}`, "small"));
      sources.append(li);
    }
    parts.push(sources);
  }
  planBox.replaceChildren(...parts);
  planBox.hidden = false;
}
