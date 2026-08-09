import { AlertTriangle, CheckCircle2, CircleAlert, Clock3, GitBranch } from "lucide-react";
import { motion, useReducedMotion } from "framer-motion";
import type { AgentRun, RunResponse } from "../types/api";
import { MonoNumber, Panel } from "./primitives";

type TraceNodeState = "completed" | "failed" | "fallback" | "rejected" | "pending";
type TraceStage = "sensor" | "log" | "fuse" | "diagnose" | "reflect" | "route" | "retry";

interface TraceNodeData {
  id: TraceStage;
  label: string;
  description: string;
  state: TraceNodeState;
  latencyMs: number | null;
  error: string | null;
}

const STAGE_AGENTS: Record<Exclude<TraceStage, "retry">, string[]> = {
  sensor: ["sensor"],
  log: ["maintenance_log", "maintenance-log", "log"],
  fuse: ["fuse", "fusion"],
  diagnose: ["coordinator", "diagnose", "diagnosis"],
  reflect: ["guardrail", "reflect", "reflection"],
  route: ["route", "router"],
};

function normaliseAgentName(agent: string) {
  return agent.toLowerCase().replaceAll("-", "_");
}

function findRuns(runs: AgentRun[], stage: Exclude<TraceStage, "retry">) {
  const names = new Set(STAGE_AGENTS[stage]);
  return runs.filter((run) => names.has(normaliseAgentName(run.agent)));
}

function isGuardrailRejection(run: AgentRun) {
  return run.guardrail_verdict?.toLowerCase() === "rejected" || run.verdict === "rejected";
}

function nodeState(run: AgentRun | undefined, stage: Exclude<TraceStage, "retry">): TraceNodeState {
  if (!run) return "pending";
  if (stage === "reflect" && isGuardrailRejection(run)) return "rejected";
  if (run.state === "failed") return "failed";
  if (run.state === "fallback") return "fallback";
  return "completed";
}

function nodeFromRun(id: Exclude<TraceStage, "retry">, label: string, description: string, run: AgentRun | undefined): TraceNodeData {
  return {
    id,
    label,
    description: run ? description : `${description}; no persisted run yet`,
    state: nodeState(run, id),
    latencyMs: run?.latency_ms ?? null,
    error: run?.error ?? null,
  };
}

function buildTraceNodes(run: RunResponse) {
  const sensorRun = findRuns(run.runs, "sensor")[0];
  const logRun = findRuns(run.runs, "log")[0];
  const fuseRun = findRuns(run.runs, "fuse")[0];
  const diagnoseRuns = findRuns(run.runs, "diagnose");
  const reflectRun = findRuns(run.runs, "reflect").find(isGuardrailRejection) ?? findRuns(run.runs, "reflect")[0];
  const routeRun = findRuns(run.runs, "route")[0];
  const rejected = Boolean(reflectRun && isGuardrailRejection(reflectRun));
  const retryRun = diagnoseRuns[1];

  const nodes: TraceNodeData[] = [
    nodeFromRun("sensor", "Sensor", "Sensor evidence", sensorRun),
    nodeFromRun("log", "Log", "Maintenance history", logRun),
    nodeFromRun("fuse", "Fuse", "Evidence fusion", fuseRun),
    nodeFromRun("diagnose", "Diagnose", "Coordinator diagnosis", diagnoseRuns[0]),
    nodeFromRun("reflect", "Reflect", "Guardrail reflection", reflectRun),
    nodeFromRun("route", "Route", "Autonomy routing", routeRun),
  ];

  if (rejected) {
    nodes.push({
      id: "retry",
      label: "Retry",
      description: retryRun ? "Coordinator retry" : "Reflection rejected; retry not persisted",
      state: retryRun ? nodeState(retryRun, "diagnose") : "pending",
      latencyMs: retryRun?.latency_ms ?? null,
      error: retryRun?.error ?? null,
    });
  }

  return { nodes, rejected };
}

function formatLatency(latencyMs: number | null) {
  if (latencyMs == null) return "—";
  return `${Number.isInteger(latencyMs) ? latencyMs : latencyMs.toFixed(1)} ms`;
}

function stateLabel(state: TraceNodeState) {
  return state === "pending" ? "PENDING" : state.toUpperCase();
}

function TraceNode({ node, className = "" }: { node: TraceNodeData; className?: string }) {
  const stateDescription = node.state === "pending" ? "not persisted" : stateLabel(node.state).toLowerCase();
  return (
    <article className={`trace-node trace-node--${node.state} ${className}`} data-stage={node.id} data-state={node.state} aria-label={`${node.label}, ${stateDescription}, latency ${formatLatency(node.latencyMs)}`}>
      <div className="trace-node-heading"><span className="trace-node-indicator" aria-hidden="true" /><span className="trace-node-label">{node.label}</span><span className="trace-node-state">{stateLabel(node.state)}</span></div>
      <p>{node.description}</p>
      <div className="trace-node-latency"><Clock3 size={13} aria-hidden="true" /><MonoNumber>{formatLatency(node.latencyMs)}</MonoNumber></div>
      {node.error && <div className="trace-node-error"><AlertTriangle size={12} aria-hidden="true" />{node.error}</div>}
    </article>
  );
}

function runStatusLabel(status: RunResponse["status"]) {
  return status === "running" ? "RUNNING" : status.toUpperCase();
}

export function AgentTraceTimeline({ run }: { run: RunResponse }) {
  const { nodes, rejected } = buildTraceNodes(run);
  const [sensor, log, fuse, diagnose, reflect, route, retry] = nodes;
  const reducedMotion = useReducedMotion() ?? false;
  const resolved = (node: TraceNodeData | undefined) => Boolean(node && node.state !== "pending");
  const connector = (d: string, visible: boolean, className = "trace-connector") => <motion.path className={className} pathLength={1} d={d} initial={{ pathLength: 0 }} animate={{ pathLength: visible ? 1 : 0 }} transition={{ duration: reducedMotion ? 0 : 0.36, ease: "easeOut" }} />;

  return (
    <Panel title="Agent trace" className="agent-trace-panel">
      <div className="agent-trace-meta"><span className="agent-trace-status"><span className={`trace-run-indicator trace-run-indicator--${run.status}`} aria-hidden="true" />{runStatusLabel(run.status)}</span><span className="agent-trace-id">trace <MonoNumber>{run.trace_id}</MonoNumber></span><span className="muted">Persisted agent latency; unavailable stages stay unfilled.</span></div>
      <div className="agent-trace-scroll">
        <div className="agent-trace-stage">
          <svg className="agent-trace-connectors" viewBox="0 0 100 100" preserveAspectRatio="none" aria-hidden="true">
            {connector("M14 27H20V30H26", resolved(sensor) && resolved(fuse))}
            {connector("M14 73H20V30H26", resolved(log) && resolved(fuse))}
            {connector("M34 30H46", resolved(fuse) && resolved(diagnose))}
            {connector("M54 30H66", resolved(diagnose) && resolved(reflect))}
            {connector("M74 30H86", resolved(reflect) && resolved(route))}
            {rejected && <>{connector("M70 38V70", resolved(reflect), "trace-connector trace-connector--retry")}{connector("M74 70H82V38H86", resolved(retry), "trace-connector trace-connector--retry")}</>}
          </svg>
          <div className="agent-trace-grid">
            <TraceNode node={sensor} className="trace-node--sensor" />
            <TraceNode node={log} className="trace-node--log" />
            <TraceNode node={fuse} className="trace-node--fuse" />
            <TraceNode node={diagnose} className="trace-node--diagnose" />
            <TraceNode node={reflect} className="trace-node--reflect" />
            <TraceNode node={route} className="trace-node--route" />
            {retry && <TraceNode node={retry} className="trace-node--retry" />}
          </div>
        </div>
      </div>
      <div className="agent-trace-legend"><span><CheckCircle2 size={13} aria-hidden="true" />Completed</span><span><CircleAlert size={13} aria-hidden="true" />Failed / fallback</span><span><GitBranch size={13} aria-hidden="true" />Amber retry fork</span></div>
    </Panel>
  );
}

export function AgentTraceLoading() {
  return <Panel title="Agent trace" className="agent-trace-panel"><div className="agent-trace-loading"><Clock3 size={16} aria-hidden="true" /><span>Loading persisted run timings…</span></div></Panel>;
}
