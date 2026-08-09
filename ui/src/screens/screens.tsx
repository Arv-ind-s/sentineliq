import { Component, lazy, Suspense, useEffect, useMemo, useRef, useState, type FormEvent, type ReactNode } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";
import { keepPreviousData, useMutation, useQueries, useQuery, useQueryClient } from "@tanstack/react-query";
import { AnimatePresence, motion, useReducedMotion, useSpring, useTransform, type MotionStyle } from "framer-motion";
import { AlertTriangle, ArrowUpRight, CheckCircle2, ChevronDown, CircleAlert, FileJson, Gauge, Info, LayoutGrid, Pause, Play, Radio, Rows3, Search, Table2, UserRound, Wrench, X } from "lucide-react";
import { CartesianGrid, Line, LineChart, ReferenceArea, ReferenceLine, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";
import type { Alert, Approval, AuditVerifyResponse, Band, Citation, ComponentStatus, DiagnosisResponse, DlqItem, EvalInjectionNote, EvalReportResponse, FaultEventSummary, HealthResponse, LedgerResponse, LlmOfflineResponse, MachineDetail, MachineSummary, MetaResponse, MetricsResponse, PredictionResponse, QuarantineEntry, QuarantineReleaseResponse, RecommendedAction, RunResponse, ScheduleWindow, TelemetryPoint, TelemetryResponse, WorkOrderSummary } from "../types/api";
import { MonoNumber, Panel, Skeleton, StatusPill } from "../components/primitives";
import { AgentTraceLoading, AgentTraceTimeline } from "../components/AgentTraceTimeline";
import { Tooltip as Hint } from "../components/Tooltip";
import { fetchJson } from "../lib/api";
import { useAuth } from "../lib/auth-context";
import { useToast } from "../lib/toast-context";
import { bandColor, bandInk, useChartTokens, type ChartTokens } from "../lib/chart-tokens";

const REPLAY_SPEEDS = [1, 60, 600] as const;
const SPARKLINE_POINTS = 60;
const INR_WHOLE_FORMATTER = new Intl.NumberFormat("en-IN", { style: "currency", currency: "INR", maximumFractionDigits: 0 });
const INR_PRECISE_FORMATTER = new Intl.NumberFormat("en-IN", { style: "currency", currency: "INR", minimumFractionDigits: 2, maximumFractionDigits: 2 });
/* Risk bands are drawn with the themed status tokens rather than fixed hex, so
 * the SVG marks stay in step with the CSS when the theme flips.
 *
 *  is the fixed status hue, used where a swatch sits next to its own
 * label.  is the per-mode ink, used for thin unlabelled marks —
 * sparkline strokes, risk rings, bar fills — because the fixed amber is only
 * 1.8:1 on a white surface and simply disappears there. */
function statusFill(tokens: ChartTokens, band: Band) {
  return bandColor(tokens, band);
}

function statusStroke(tokens: ChartTokens, band: Band) {
  return bandInk(tokens, band);
}

const DETAIL_CHANNELS = [
  "vibration_rms_mm_s",
  "vibration_kurtosis",
  "temp_bearing_c",
  "temp_motor_c",
  "pressure_bar",
  "current_a",
  "rpm",
  "ambient_temp_c",
] as const;

const CHANNEL_LABELS: Record<string, string> = {
  vibration_rms_mm_s: "Vibration RMS",
  vibration_kurtosis: "Vibration kurtosis",
  temp_bearing_c: "Bearing temperature",
  temp_motor_c: "Motor temperature",
  pressure_bar: "Pressure",
  current_a: "Motor current",
  rpm: "Shaft speed",
  ambient_temp_c: "Ambient temperature",
};

const CHANNEL_UNITS: Record<string, string> = {
  vibration_rms_mm_s: "mm/s",
  vibration_kurtosis: "×",
  temp_bearing_c: "°C",
  temp_motor_c: "°C",
  pressure_bar: "bar",
  current_a: "A",
  rpm: "rpm",
  ambient_temp_c: "°C",
};

/* The eight channel charts are small multiples, each with a single series and
 * its own title. Colour therefore carries no information between them, so they
 * all share one series token: the previous per-channel rainbow encoded nothing
 * and made the screen read as decoration rather than instrumentation. */
const QUALITY_FLAGS = ["interpolated", "despiked", "missing"] as const;
type QualityKind = (typeof QUALITY_FLAGS)[number];
/* Data-quality flags are an ordered severity, not a categorical series. */
const QUALITY_TOKENS: Record<QualityKind, keyof ChartTokens> = { interpolated: "warn", despiked: "high", missing: "crit" };
type DetailPoint = { ts: number; quality_flags: string[]; [channel: string]: string | number | string[] | null };
type QualitySegment = { kind: QualityKind; start: number; end: number };

type FleetSort = "risk" | "rul" | "id" | "quality";
type FleetView = "grid" | "table";
const FLEET_BANDS: readonly Band[] = ["CRITICAL", "HIGH", "MEDIUM", "LOW", "NODATA"];
const ALERT_SEVERITIES = ["CRITICAL", "HIGH", "MEDIUM", "LOW"] as const;
const DigitalTwin = lazy(() => import("../components/DigitalTwin").then(({ DigitalTwin: Twin }) => ({ default: Twin })));

/** 2D fallback slots, in the same fixed anatomy order the API returns: bearing, shaft, seal,
 *  motor, driven element. Labels come from the machine's own component names, not from here. */
const SCHEMATIC_LAYOUT = [
  { path: "M34 135a18 18 0 1 0 36 0a18 18 0 1 0-36 0Z M46 135a6 6 0 1 0 12 0a6 6 0 1 0-12 0Z", labelX: 52, labelY: 184 },
  { path: "M68 127H281V143H68Z", labelX: 174, labelY: 112 },
  { path: "M98 108L124 108L132 170L106 170Z", labelX: 115, labelY: 194 },
  { path: "M238 66H314Q328 66 328 80V190Q328 204 314 204H238Z", labelX: 282, labelY: 228 },
  { path: "M169 101C187 87 212 92 220 111C228 130 218 153 196 159C176 165 156 151 154 132C152 119 158 109 169 101Z", labelX: 188, labelY: 181 },
] as const;

function titleCase(name: string) {
  return name.charAt(0).toUpperCase() + name.slice(1);
}

function ScreenHeader({ kicker, title, description }: { kicker: string; title: string; description: string }) {
  return <header className="screen-header"><div className="eyebrow">{kicker}</div><h1>{title}</h1><p>{description}</p></header>;
}

function clamp(value: number, min: number, max: number) {
  return Math.min(max, Math.max(min, value));
}

function Sparkline({ machine, values, pending, tokens }: { machine: MachineSummary; values: number[]; pending: boolean; tokens: ChartTokens }) {
  if (pending) return <div className="sparkline sparkline--loading"><Skeleton width="100%" height="30px" /></div>;
  if (values.length < 2) return <div className="sparkline sparkline--empty"><span>Waiting for telemetry</span></div>;
  const minimum = Math.min(...values);
  const maximum = Math.max(...values);
  const range = maximum - minimum || Math.max(Math.abs(maximum), 1);
  const points = values.map((value, index) => `${(index / (values.length - 1)) * 180},${32 - clamp((value - minimum) / range, 0, 1) * 27}`).join(" ");
  const color = statusStroke(tokens, machine.band);
  return (
    <div className="sparkline" aria-label={`${machine.id} vibration trend, ${values.length} points`}>
      <svg viewBox="0 0 180 36" role="img" aria-label={`${machine.id} vibration sparkline`} preserveAspectRatio="none" data-point-count={values.length}>
        <polyline points={points} fill="none" vectorEffect="non-scaling-stroke" style={{ stroke: color }} />
      </svg>
    </div>
  );
}

function RiskRing({ machine, tokens }: { machine: MachineSummary; tokens: ChartTokens }) {
  const risk = clamp(machine.risk, 0, 100);
  const reducedMotion = useReducedMotion() ?? false;
  const riskValue = useSpring(risk, { stiffness: 120, damping: 20 });
  const riskSweep = useTransform(riskValue, (value) => `${value}%`);

  useEffect(() => {
    if (reducedMotion) riskValue.jump(risk);
  }, [reducedMotion, risk, riskValue]);

  const style = {
    "--risk-sweep": reducedMotion ? `${risk}%` : riskSweep,
    "--ring-color": statusStroke(tokens, machine.band),
  } as unknown as MotionStyle;
  return (
    <motion.div className="risk-ring" style={style} role="img" aria-label={`${machine.id} risk ${Math.round(risk)} out of 100`}>
      <span><MonoNumber>{Math.round(risk)}</MonoNumber><small>RISK</small></span>
    </motion.div>
  );
}

/* The entrance used framer-motion variants propagated from the grid. Those
 * variants never resolved, so every tile stayed at the `hidden` opacity of 0 and
 * the fleet was invisible. A CSS keyframe cannot get stuck part-way, so the
 * tiles are always painted even if the animation never runs. */
function MachineTile({ machine, sparkline, sparklinePending, index, tokens }: { machine: MachineSummary; sparkline: number[]; sparklinePending: boolean; index: number; tokens: ChartTokens }) {
  const statusLabel = machine.band === "NODATA" ? "No data" : machine.band;
  return (
    <Link className="machine-tile" style={{ animationDelay: `${Math.min(index, 14) * 28}ms` }} to={`/machines/${machine.id}`} aria-label={`Open ${machine.id} machine detail`}>
      <div className="machine-tile-top">
        <div><span className="machine-id">{machine.id}</span><h3>{machine.name}</h3></div>
        <StatusPill status={machine.band} label={statusLabel} />
      </div>
      <div className="risk-row"><RiskRing machine={machine} tokens={tokens} /><div className="machine-facts"><div><span className="eyebrow">P.fail / 24h</span><MonoNumber>{Math.round(machine.p_failure_24h * 100)}%</MonoNumber></div><div><span className="eyebrow">RUL</span><MonoNumber>{machine.rul_days}d</MonoNumber></div></div></div>
      <Sparkline machine={machine} values={sparkline} pending={sparklinePending} tokens={tokens} />
      <div className="tile-footer"><span>{machine.line}</span><span>quality <MonoNumber>{Math.round(machine.quality * 100)}%</MonoNumber></span><ArrowUpRight size={16} aria-hidden="true" /></div>
    </Link>
  );
}

function MachineRow({ machine, tokens }: { machine: MachineSummary; tokens: ChartTokens }) {
  return (
    <tr>
      <th scope="row"><Link className="fleet-row-link" to={`/machines/${machine.id}`}>{machine.id}</Link><small>{machine.name}</small></th>
      <td><StatusPill status={machine.band} label={machine.band === "NODATA" ? "No data" : machine.band} /></td>
      <td>
        <div className="fleet-risk-cell">
          <span className="fleet-risk-track"><span style={{ width: `${clamp(machine.risk, 0, 100)}%`, background: statusStroke(tokens, machine.band) }} /></span>
          <MonoNumber>{Math.round(machine.risk)}</MonoNumber>
        </div>
      </td>
      <td><MonoNumber>{Math.round(machine.p_failure_24h * 100)}%</MonoNumber></td>
      <td><MonoNumber>{machine.rul_days}d</MonoNumber></td>
      <td><MonoNumber>{Math.round(machine.quality * 100)}%</MonoNumber></td>
      <td>{machine.line}</td>
    </tr>
  );
}

const FLEET_SORTS: Array<{ value: FleetSort; label: string }> = [
  { value: "risk", label: "Risk (high to low)" },
  { value: "rul", label: "RUL (soonest first)" },
  { value: "quality", label: "Quality (lowest first)" },
  { value: "id", label: "Asset ID (A–Z)" },
];

function FleetToolbar({ query, onQuery, bands, onToggleBand, sort, onSort, view, onView, shown, total }: {
  query: string;
  onQuery: (value: string) => void;
  bands: Set<Band>;
  onToggleBand: (band: Band) => void;
  sort: FleetSort;
  onSort: (sort: FleetSort) => void;
  view: FleetView;
  onView: (view: FleetView) => void;
  shown: number;
  total: number;
}) {
  return (
    <div className="fleet-toolbar">
      <div className="fleet-search">
        <Search size={14} aria-hidden="true" />
        <input type="search" value={query} placeholder="Filter by asset, name or line" aria-label="Filter machines" onChange={(event) => onQuery(event.target.value)} />
        {query && <button type="button" className="fleet-search-clear" onClick={() => onQuery("")} aria-label="Clear filter"><X size={13} aria-hidden="true" /></button>}
      </div>
      <div className="fleet-bands" role="group" aria-label="Filter by risk band">
        {FLEET_BANDS.map((band) => (
          <button
            key={band}
            type="button"
            aria-pressed={bands.has(band)}
            className={`fleet-band fleet-band--${band.toLowerCase()}${bands.has(band) ? " fleet-band--on" : ""}`}
            onClick={() => onToggleBand(band)}
          >
            <span className="fleet-band-dot" aria-hidden="true" />
            {band === "NODATA" ? "No data" : band.charAt(0) + band.slice(1).toLowerCase()}
          </button>
        ))}
      </div>
      <label className="fleet-sort">
        <span className="sr-only">Sort machines</span>
        <select value={sort} onChange={(event) => onSort(event.target.value as FleetSort)}>
          {FLEET_SORTS.map((option) => <option key={option.value} value={option.value}>{option.label}</option>)}
        </select>
        <ChevronDown size={13} aria-hidden="true" />
      </label>
      <div className="fleet-view" role="group" aria-label="Fleet layout">
        <Hint content="Card grid"><button type="button" aria-pressed={view === "grid"} aria-label="Card grid" className={`fleet-view-option${view === "grid" ? " fleet-view-option--on" : ""}`} onClick={() => onView("grid")}><LayoutGrid size={14} aria-hidden="true" /></button></Hint>
        <Hint content="Compact table"><button type="button" aria-pressed={view === "table"} aria-label="Compact table" className={`fleet-view-option${view === "table" ? " fleet-view-option--on" : ""}`} onClick={() => onView("table")}><Rows3 size={14} aria-hidden="true" /></button></Hint>
      </div>
      <span className="fleet-count" aria-live="polite">{shown} of {total}</span>
    </div>
  );
}

function MachineTileSkeleton() {
  return <div className="machine-tile machine-tile--skeleton" aria-hidden="true"><div className="machine-tile-top"><div><Skeleton width="72px" height="12px" /><Skeleton width="126px" height="12px" /></div><Skeleton width="72px" height="24px" /></div><div className="risk-row"><Skeleton width="74px" height="74px" className="skeleton-circle" /><div className="machine-facts"><Skeleton width="54px" height="28px" /><Skeleton width="46px" height="28px" /></div></div><Skeleton width="100%" height="36px" /><div className="tile-footer"><Skeleton width="52px" height="10px" /><Skeleton width="86px" height="10px" /></div></div>;
}

function AlertItem({ alert }: { alert: Alert }) {
  const { user } = useAuth();
  const canDiagnose = user?.role !== "operator";
  const content = <>
      <span className={`alert-severity alert-severity--${alert.severity.toLowerCase()}`} aria-hidden="true" />
      <span className="alert-item-body"><span className="alert-item-title">{alert.title}</span><span className="alert-item-message">{alert.message}</span><span className="alert-item-time"><MonoNumber>{alert.state.toUpperCase()}</MonoNumber> · {alert.kind} alert</span></span>
      {canDiagnose ? <ArrowUpRight size={15} aria-hidden="true" /> : <Info size={15} aria-label="Diagnosis requires technician access" />}
    </>;
  return canDiagnose
    ? <Link className="alert-item" to={`/diagnosis/new-${alert.machine_id.toLowerCase()}`}>{content}</Link>
    : <div className="alert-item alert-item--readonly" title="Diagnosis requires technician, planner, or administrator access.">{content}</div>;
}

function AlertSkeleton() {
  return <div className="alert-item alert-item--skeleton" aria-hidden="true"><Skeleton width="8px" height="8px" className="skeleton-circle" /><span className="alert-item-body"><Skeleton width="90%" height="13px" /><Skeleton width="100%" height="11px" /><Skeleton width="56%" height="10px" /></span></div>;
}

/** Alerts arrive one row per machine per rule, so an unfiltered feed is mostly
 *  the same sentence fifteen times. Grouping by machine collapses that into one
 *  row per asset with the worst severity surfaced and the rest behind a count. */
type AlertGroup = { machineId: string; alerts: Alert[]; worst: Alert };

function severityRank(severity: string) {
  const index = (ALERT_SEVERITIES as readonly string[]).indexOf(severity.toUpperCase());
  return index === -1 ? ALERT_SEVERITIES.length : index;
}

function groupAlerts(alerts: Alert[]): AlertGroup[] {
  const byMachine = new Map<string, Alert[]>();
  for (const alert of alerts) {
    const bucket = byMachine.get(alert.machine_id);
    if (bucket) bucket.push(alert);
    else byMachine.set(alert.machine_id, [alert]);
  }
  return [...byMachine.entries()]
    .map(([machineId, machineAlerts]) => {
      const sorted = [...machineAlerts].sort((left, right) => severityRank(left.severity) - severityRank(right.severity));
      return { machineId, alerts: sorted, worst: sorted[0] };
    })
    .sort((left, right) => severityRank(left.worst.severity) - severityRank(right.worst.severity) || left.machineId.localeCompare(right.machineId));
}

function AlertGroupItem({ group }: { group: AlertGroup }) {
  const [expanded, setExpanded] = useState(false);
  const extra = group.alerts.length - 1;
  return (
    <div className="alert-group">
      <AlertItem alert={group.worst} />
      {extra > 0 && (
        <>
          <button className="alert-group-toggle" type="button" aria-expanded={expanded} onClick={() => setExpanded((open) => !open)}>
            <ChevronDown size={12} aria-hidden="true" className={expanded ? "alert-group-chevron alert-group-chevron--open" : "alert-group-chevron"} />
            {expanded ? "Hide" : `${extra} more on ${group.machineId}`}
          </button>
          {expanded && group.alerts.slice(1).map((alert) => <AlertItem key={alert.id} alert={alert} />)}
        </>
      )}
    </div>
  );
}

function KpiCard({ label, value, note, pending = false }: { label: string; value: string; note: string; pending?: boolean }) {
  return <Panel className="kpi-card"><span className="eyebrow">{label}</span>{pending ? <Skeleton width="88px" height="32px" /> : <MonoNumber>{value}</MonoNumber>}<span className="kpi-note">{note}</span></Panel>;
}

function ReplayControl({ speed, paused, onSpeedChange, onPause }: { speed: number; paused: boolean; onSpeedChange: (speed: number) => void; onPause: () => void }) {
  return (
    <div className="replay-controls" aria-label="Replay controls">
      <label className="replay-speed-label" htmlFor="replay-speed"><Gauge size={15} aria-hidden="true" /> Replay</label>
      <select id="replay-speed" className="replay-speed" value={speed} onChange={(event) => onSpeedChange(Number(event.target.value))} aria-label="Replay speed">
        {REPLAY_SPEEDS.map((option) => <option key={option} value={option}>{option}×</option>)}
      </select>
      <button className="quiet-button replay-pause" type="button" onClick={onPause} aria-pressed={paused}>{paused ? <Play size={14} aria-hidden="true" /> : <Pause size={14} aria-hidden="true" />}{paused ? "Resume" : "Pause"}</button>
    </div>
  );
}

export function FloorScreen() {
  const reducedMotion = useReducedMotion() ?? false;
  const tokens = useChartTokens();
  const toast = useToast();
  const machines = useQuery({ queryKey: ["machines"], queryFn: () => fetchJson<MachineSummary[]>("/machines"), placeholderData: (previous) => previous, refetchInterval: 15_000 });
  const alerts = useQuery({ queryKey: ["alerts", "open"], queryFn: () => fetchJson<Alert[]>("/alerts?state=open"), placeholderData: (previous) => previous, refetchInterval: 5_000 });
  const ledger = useQuery({ queryKey: ["ledger", "floor"], queryFn: () => fetchJson<LedgerResponse>("/ledger"), staleTime: 30_000 });
  const telemetryQueries = useQueries({ queries: (machines.data ?? []).map((machine) => ({
    queryKey: ["live-telemetry", machine.id],
    queryFn: async () => {
      const response = await fetchJson<TelemetryResponse>(`/machines/${machine.id}/telemetry?interval=60&channels=vibration_rms_mm_s`);
      return response.series.map((point) => point.channels.vibration_rms_mm_s).filter((value): value is number => typeof value === "number" && Number.isFinite(value)).slice(-SPARKLINE_POINTS);
    },
    staleTime: 30_000,
  })) });
  const [replaySpeed, setReplaySpeed] = useState(600);
  const [paused, setPaused] = useState(false);
  const [fleetQuery, setFleetQuery] = useState("");
  const [fleetBands, setFleetBands] = useState<Set<Band>>(() => new Set());
  const [fleetSort, setFleetSort] = useState<FleetSort>("risk");
  const [fleetView, setFleetView] = useState<FleetView>("grid");
  const [alertSeverity, setAlertSeverity] = useState<string | null>(null);
  const [alertGrouped, setAlertGrouped] = useState(true);

  const atRisk = machines.data?.filter((machine) => machine.risk >= 55).length ?? 0;
  const predictedFailures = machines.data?.filter((machine) => machine.rul_days <= 7).length ?? 0;
  const fleetQuality = machines.data?.length ? Math.round(machines.data.reduce((sum, machine) => sum + machine.quality, 0) / machines.data.length * 100) : 0;
  const alertsData = useMemo(() => alerts.data ?? [], [alerts.data]);
  const policies = ledger.data?.policies ?? [];
  const reactive = policies.find((policy) => policy.policy === "reactive");
  const predictive = policies.find((policy) => policy.policy === "predictive");
  const downtimeAvoided = reactive && predictive ? Math.max(0, reactive.downtime_minutes - predictive.downtime_minutes) : null;
  const savings = predictive?.avoided_cost_inr ?? null;

  const updateReplaySpeed = (speed: number) => {
    const previous = replaySpeed;
    setReplaySpeed(speed);
    void fetchJson(`/stream/speed?speed=${speed}`, { method: "POST" }).catch(() => {
      setReplaySpeed(previous);
      toast.push("Replay speed was not applied.", { tone: "error", detail: "The stream service rejected the change; the previous speed is still running." });
    });
  };

  const updateReplayPause = (nextPaused: boolean) => {
    const previous = paused;
    setPaused(nextPaused);
    void fetchJson(`/stream/pause?paused=${nextPaused}`, { method: "POST" }).catch(() => {
      setPaused(previous);
      toast.push(nextPaused ? "Replay could not be paused." : "Replay could not be resumed.", { tone: "error", detail: "The stream service did not accept the command." });
    });
  };

  // Sparklines are indexed against the unfiltered machine list, so the lookup is
  // built once by id rather than by position in the filtered view.
  const sparklines = useMemo(() => {
    const byMachine = new Map<string, { values: number[]; pending: boolean }>();
    (machines.data ?? []).forEach((machine, index) => {
      byMachine.set(machine.id, { values: telemetryQueries[index]?.data ?? [], pending: telemetryQueries[index]?.isPending ?? true });
    });
    return byMachine;
  }, [machines.data, telemetryQueries]);

  const visibleMachines = useMemo(() => {
    const needle = fleetQuery.trim().toLowerCase();
    const filtered = (machines.data ?? []).filter((machine) => {
      if (fleetBands.size && !fleetBands.has(machine.band)) return false;
      if (!needle) return true;
      return `${machine.id} ${machine.name} ${machine.line} ${machine.type}`.toLowerCase().includes(needle);
    });
    const sorted = [...filtered];
    sorted.sort((left, right) => {
      switch (fleetSort) {
        case "rul": return left.rul_days - right.rul_days || right.risk - left.risk;
        case "quality": return left.quality - right.quality || right.risk - left.risk;
        case "id": return left.id.localeCompare(right.id);
        default: return right.risk - left.risk || left.id.localeCompare(right.id);
      }
    });
    return sorted;
  }, [fleetBands, fleetQuery, fleetSort, machines.data]);

  const toggleBand = (band: Band) => setFleetBands((current) => {
    const next = new Set(current);
    if (next.has(band)) next.delete(band);
    else next.add(band);
    return next;
  });

  const alertsFiltered = useMemo(
    () => (alertSeverity ? alertsData.filter((alert) => alert.severity.toUpperCase() === alertSeverity) : alertsData),
    [alertSeverity, alertsData],
  );
  const alertGroups = useMemo(() => groupAlerts(alertsFiltered), [alertsFiltered]);
  const severityCounts = useMemo(() => {
    const counts = new Map<string, number>();
    for (const alert of alertsData) {
      const key = alert.severity.toUpperCase();
      counts.set(key, (counts.get(key) ?? 0) + 1);
    }
    return counts;
  }, [alertsData]);

  return (
    <div className="screen">
      <ScreenHeader kicker="Plant / live telemetry" title="Live Floor" description="A calm, current view of machine health across the synthetic plant." />
      <div className="toolbar"><div className="toolbar-status"><Radio size={15} aria-hidden="true" /> {paused ? "Replay paused" : `Replay clock active · ${replaySpeed}×`}</div><ReplayControl speed={replaySpeed} paused={paused} onSpeedChange={updateReplaySpeed} onPause={() => updateReplayPause(!paused)} /></div>
      <div className="kpi-grid">
        <KpiCard label="Machines at risk" value={String(atRisk)} note="high or critical band" pending={machines.isPending} />
        <KpiCard label="Predicted failures / 7d" value={String(predictedFailures).padStart(2, "0")} note="current model horizon" pending={machines.isPending} />
        <KpiCard label="Downtime avoided" value={downtimeAvoided == null ? "No data" : `${downtimeAvoided.toLocaleString("en-GB")} min`} note="predictive vs reactive" pending={machines.isPending || ledger.isPending} />
        <KpiCard label="INR saved" value={savings == null ? "No data" : formatInr(savings)} note={`fleet quality ${fleetQuality}%`} pending={machines.isPending || ledger.isPending} />
      </div>
      <div className="floor-grid">
        <Panel title="Fleet status" className="fleet-panel">
          {!machines.isPending && !machines.isError && Boolean(machines.data?.length) && (
            <FleetToolbar
              query={fleetQuery}
              onQuery={setFleetQuery}
              bands={fleetBands}
              onToggleBand={toggleBand}
              sort={fleetSort}
              onSort={setFleetSort}
              view={fleetView}
              onView={setFleetView}
              shown={visibleMachines.length}
              total={machines.data?.length ?? 0}
            />
          )}
          {machines.isPending
            ? <div className="machine-grid">{Array.from({ length: 15 }, (_, index) => <MachineTileSkeleton key={index} />)}</div>
            : machines.isError
              ? <div className="empty-state"><AlertTriangle size={20} aria-hidden="true" /><strong>Machine data unavailable</strong><span>Connect the backend at the configured local API and retry.</span></div>
              : !machines.data?.length
                ? <div className="empty-state"><Info size={20} aria-hidden="true" /><strong>No machines returned</strong><span>The machine registry is reachable, but it contains no assets to display.</span></div>
                : !visibleMachines.length
                  ? <div className="empty-state empty-state--compact"><Search size={20} aria-hidden="true" /><strong>No machines match these filters</strong><span>Clear the search or risk-band filters to see the full fleet.</span><button className="quiet-button" type="button" onClick={() => { setFleetQuery(""); setFleetBands(new Set()); }}>Reset filters</button></div>
                  : fleetView === "table"
                    ? <div className="fleet-table-wrap">
                        <table className="fleet-table">
                          <caption className="sr-only">Fleet status, {visibleMachines.length} machines</caption>
                          <thead><tr><th scope="col">Asset</th><th scope="col">Band</th><th scope="col">Risk</th><th scope="col">P.fail 24h</th><th scope="col">RUL</th><th scope="col">Quality</th><th scope="col">Line</th></tr></thead>
                          <tbody>{visibleMachines.map((machine) => <MachineRow key={machine.id} machine={machine} tokens={tokens} />)}</tbody>
                        </table>
                      </div>
                    : <motion.div className="machine-grid" transition={{ staggerChildren: 0.04 }}>
                        {visibleMachines.map((machine, index) => (
                          <MachineTile
                            key={machine.id}
                            machine={machine}
                            index={index}
                            tokens={tokens}
                            sparkline={sparklines.get(machine.id)?.values ?? []}
                            sparklinePending={sparklines.get(machine.id)?.pending ?? true}
                          />
                        ))}
                      </motion.div>}
        </Panel>
        <Panel
          title="Live alerts"
          className="alerts-panel"
          action={alertsData.length > 1 ? <button className="chart-table-toggle" type="button" aria-pressed={alertGrouped} onClick={() => setAlertGrouped((grouped) => !grouped)}>{alertGrouped ? "Ungroup" : "Group by machine"}</button> : undefined}
        >
          {Boolean(alertsData.length) && (
            <div className="alert-filter" role="group" aria-label="Filter alerts by severity">
              <button type="button" aria-pressed={alertSeverity === null} className={`alert-filter-chip${alertSeverity === null ? " alert-filter-chip--on" : ""}`} onClick={() => setAlertSeverity(null)}>All <b>{alertsData.length}</b></button>
              {ALERT_SEVERITIES.filter((severity) => severityCounts.get(severity)).map((severity) => (
                <button
                  key={severity}
                  type="button"
                  aria-pressed={alertSeverity === severity}
                  className={`alert-filter-chip alert-filter-chip--${severity.toLowerCase()}${alertSeverity === severity ? " alert-filter-chip--on" : ""}`}
                  onClick={() => setAlertSeverity(alertSeverity === severity ? null : severity)}
                >
                  <span className={`alert-severity alert-severity--${severity.toLowerCase()}`} aria-hidden="true" />
                  {severity.charAt(0) + severity.slice(1).toLowerCase()} <b>{severityCounts.get(severity)}</b>
                </button>
              ))}
            </div>
          )}
          <div className="alert-feed" aria-live="polite" aria-atomic="false">
            {alerts.isPending
              ? <>{Array.from({ length: 3 }, (_, index) => <AlertSkeleton key={index} />)}</>
              : alerts.isError
                ? <div className="empty-state empty-state--compact"><AlertTriangle size={20} aria-hidden="true" /><strong>Alert feed unavailable</strong><span>Machine status remains visible while alerts reconnect.</span></div>
                : !alertsFiltered.length
                  ? <div className="empty-state empty-state--compact"><span className="alert-marker" aria-hidden="true" /><strong>{alertsData.length ? "No alerts at this severity" : "No open alerts"}</strong><span>{alertsData.length ? "Choose another severity to see the rest of the feed." : "The fleet is being monitored continuously."}</span></div>
                  : <AnimatePresence initial={false} mode="popLayout">
                      {(alertGrouped ? alertGroups : alertsFiltered.map((alert) => ({ machineId: alert.machine_id, alerts: [alert], worst: alert }))).map((group) => (
                        <motion.div
                          className="alert-motion-item"
                          key={`${group.machineId}-${group.worst.id}`}
                          initial={reducedMotion ? false : { opacity: 0, x: 12 }}
                          animate={{ opacity: 1, x: 0 }}
                          exit={{ opacity: 0, height: 0, marginTop: 0 }}
                          transition={{ duration: reducedMotion ? 0 : 0.24, ease: "easeOut" }}
                        >
                          <AlertGroupItem group={group} />
                        </motion.div>
                      ))}
                    </AnimatePresence>}
          </div>
        </Panel>
      </div>
    </div>
  );
}

function formatTimestamp(timestamp: number | string) {
  return new Intl.DateTimeFormat("en-GB", { day: "2-digit", month: "short", hour: "2-digit", minute: "2-digit" }).format(new Date(timestamp));
}

function useCountUp(target: number, reducedMotion: boolean) {
  const [value, setValue] = useState(0);
  const previousTarget = useRef(0);

  useEffect(() => {
    const from = previousTarget.current;
    const started = performance.now();
    const duration = reducedMotion ? 0 : 560;
    let frame = 0;
    const tick = (now: number) => {
      const progress = duration === 0 ? 1 : clamp((now - started) / duration, 0, 1);
      const eased = 1 - Math.pow(1 - progress, 3);
      setValue(from + (target - from) * eased);
      if (progress < 1) {
        frame = requestAnimationFrame(tick);
      } else {
        previousTarget.current = target;
      }
    };
    frame = requestAnimationFrame(tick);
    return () => cancelAnimationFrame(frame);
  }, [reducedMotion, target]);

  return value;
}

function formatInr(value: number, decimalPlaces = 0) {
  return (decimalPlaces === 0 ? INR_WHOLE_FORMATTER : INR_PRECISE_FORMATTER).format(value);
}

function eventWindow(detail: MachineDetail) {
  const event = detail.fault_events.at(-1);
  const end = event ? new Date(event.onset_ts).getTime() + 12 * 60 * 60 * 1000 : new Date(detail.updated_at).getTime();
  return { start: end - 24 * 60 * 60 * 1000, end };
}

function downsample(points: DetailPoint[], channel: string, maximum = 600): DetailPoint[] {
  if (points.length <= maximum || maximum < 3) return points;
  const sampled = [points[0]];
  const bucketSize = (points.length - 2) / (maximum - 2);
  let previousIndex = 0;

  for (let bucket = 0; bucket < maximum - 2; bucket += 1) {
    const averageStart = Math.floor((bucket + 1) * bucketSize) + 1;
    const averageEnd = Math.min(Math.floor((bucket + 2) * bucketSize) + 1, points.length);
    const averagePoints = points.slice(averageStart, averageEnd);
    const averageX = averagePoints.length ? averagePoints.reduce((sum, point) => sum + point.ts, 0) / averagePoints.length : points[points.length - 1].ts;
    const averageY = averagePoints.length ? averagePoints.reduce((sum, point) => sum + Number(point[channel] ?? 0), 0) / averagePoints.length : Number(points[points.length - 1][channel] ?? 0);
    const rangeStart = Math.floor(bucket * bucketSize) + 1;
    const rangeEnd = Math.min(Math.floor((bucket + 1) * bucketSize) + 1, points.length - 1);
    const anchor = points[previousIndex];
    let selectedIndex = rangeStart;
    let largestArea = -1;
    for (let index = rangeStart; index < rangeEnd; index += 1) {
      const point = points[index];
      const area = Math.abs((anchor.ts - averageX) * (Number(point[channel] ?? 0) - Number(anchor[channel] ?? 0)) - (anchor.ts - point.ts) * (averageY - Number(anchor[channel] ?? 0))) * 0.5;
      if (area > largestArea) {
        largestArea = area;
        selectedIndex = index;
      }
    }
    sampled.push(points[selectedIndex]);
    previousIndex = selectedIndex;
  }
  sampled.push(points[points.length - 1]);
  return sampled;
}

function normalizePoints(series: TelemetryPoint[], channels: readonly string[]) {
  return [...series]
    .sort((left, right) => new Date(left.ts).getTime() - new Date(right.ts).getTime())
    .map((point) => {
      const normalized: DetailPoint = { ts: new Date(point.ts).getTime(), quality_flags: point.quality_flags };
      channels.forEach((channel) => { normalized[channel] = Number.isFinite(point.channels[channel]) ? point.channels[channel] : null; });
      return normalized;
    });
}

function qualitySegments(points: DetailPoint[], start: number, end: number): QualitySegment[] {
  const segments: QualitySegment[] = [];
  points.forEach((point, index) => {
    const flag = point.quality_flags.map((value) => value.toLowerCase()).find((value): value is QualityKind => (QUALITY_FLAGS as readonly string[]).includes(value));
    if (!flag) return;
    const segmentStart = Math.max(start, point.ts);
    const segmentEnd = Math.min(end, points[index + 1]?.ts ?? end);
    if (segmentEnd <= segmentStart) return;
    const previous = segments.at(-1);
    if (previous && previous.kind === flag && previous.end === segmentStart) previous.end = segmentEnd;
    else segments.push({ kind: flag, start: segmentStart, end: segmentEnd });
  });
  return segments;
}

function qualityKindFor(flags: string[]) {
  const normalized = flags.map((flag) => flag.toLowerCase());
  return QUALITY_FLAGS.find((kind) => normalized.includes(kind));
}

function QualityRibbon({ segments, start, end }: { segments: QualitySegment[]; start: number; end: number }) {
  return (
    <div className="quality-ribbon-wrap">
      <div className="quality-ribbon" role="img" aria-label="Telemetry quality ribbon">
        {segments.map((segment) => <span key={`${segment.kind}-${segment.start}`} className={`quality-ribbon-segment quality-ribbon-segment--${segment.kind}`} style={{ left: `${((segment.start - start) / (end - start)) * 100}%`, width: `${((segment.end - segment.start) / (end - start)) * 100}%` }} title={`${segment.kind} · ${formatTimestamp(segment.start)}`} />)}
      </div>
      <div className="quality-legend" aria-label="Quality flag legend">
        {QUALITY_FLAGS.map((kind) => <span key={kind}><i className={`quality-swatch quality-swatch--${kind}`} aria-hidden="true" />{kind}</span>)}
      </div>
    </div>
  );
}

function ChartTable({ channel, points }: { channel: string; points: DetailPoint[] }) {
  return (
    <div className="chart-table-wrap">
      <table className="chart-table">
        <caption>{CHANNEL_LABELS[channel]} readings</caption>
        <thead><tr><th scope="col">Timestamp</th><th scope="col">Value ({CHANNEL_UNITS[channel]})</th><th scope="col">Quality</th></tr></thead>
        <tbody>{points.map((point) => <tr key={`${channel}-${point.ts}`}><td><MonoNumber>{formatTimestamp(point.ts)}</MonoNumber></td><td>{point[channel] == null ? "—" : Number(point[channel]).toFixed(2)}</td><td>{qualityKindFor(point.quality_flags) ?? "nominal"}</td></tr>)}</tbody>
      </table>
    </div>
  );
}

function ChannelChart({ channel, points, events, segments, start, end }: { channel: string; points: DetailPoint[]; events: FaultEventSummary[]; segments: QualitySegment[]; start: number; end: number }) {
  const [tableOpen, setTableOpen] = useState(false);
  const tokens = useChartTokens();
  return (
    <Panel className="channel-panel" title={`${CHANNEL_LABELS[channel]} · ${CHANNEL_UNITS[channel]}`} action={<button className="chart-table-toggle" type="button" aria-expanded={tableOpen} onClick={() => setTableOpen((open) => !open)}><Table2 size={14} aria-hidden="true" />{tableOpen ? "Chart" : "View as table"}</button>}>
      {tableOpen ? <ChartTable channel={channel} points={points} /> : <div className="chart-container"><ResponsiveContainer width="100%" height={164}><LineChart data={points} syncId="machine-detail" syncMethod="value" margin={{ top: 12, right: 12, bottom: 0, left: 0 }}>
        <CartesianGrid stroke={tokens.grid} strokeDasharray="2 3" vertical={false} />
        <XAxis dataKey="ts" type="number" domain={[start, end]} tickFormatter={(value) => formatTimestamp(value)} tick={{ fill: tokens.tick, fontSize: 10 }} tickLine={false} axisLine={{ stroke: tokens.axis }} minTickGap={48} />
        <YAxis tick={{ fill: tokens.tick, fontSize: 10 }} tickLine={false} axisLine={false} width={44} domain={["auto", "auto"]} />
        <Tooltip labelFormatter={(value) => formatTimestamp(Number(value))} contentStyle={{ background: tokens.tooltipBg, border: `1px solid ${tokens.border}`, borderRadius: 6, color: tokens.text, fontSize: 11, boxShadow: "0 2px 10px rgba(0,0,0,.18)" }} cursor={{ stroke: tokens.axis, strokeWidth: 1 }} />
        {segments.map((segment) => <ReferenceArea key={`${channel}-${segment.kind}-${segment.start}`} x1={segment.start} x2={segment.end} fill={tokens[QUALITY_TOKENS[segment.kind]]} fillOpacity={0.12} ifOverflow="extendDomain" />)}
        {events.filter((event) => { const onset = new Date(event.onset_ts).getTime(); return onset >= start && onset <= end; }).map((event) => <ReferenceLine key={`${channel}-event-${event.event_id}`} x={new Date(event.onset_ts).getTime()} stroke={tokens.crit} strokeDasharray="4 3" label={{ value: "FAULT", fill: tokens.crit, fontSize: 9, position: "insideTop" }} />)}
        <Line type="monotone" dataKey={channel} name={CHANNEL_LABELS[channel]} stroke={tokens.series} strokeWidth={1.8} dot={false} isAnimationActive={false} connectNulls={false} />
      </LineChart></ResponsiveContainer></div>}
    </Panel>
  );
}

const WORK_ORDER_PAGE = 8;

/** Work orders span months while the telemetry window is 24 hours, so the old
 *  proportional rail clamped almost every card to one of the two ends and then
 *  stacked them, producing a several-thousand-pixel column of overlapping
 *  boxes. History is inherently a list, so it is rendered as one — newest
 *  first, with the few orders that do fall inside the plotted window marked so
 *  the link to the charts above survives. */
function WorkOrderRail({ orders, start, end }: { orders: WorkOrderSummary[]; start: number; end: number }) {
  const [expanded, setExpanded] = useState(false);
  const sorted = useMemo(
    () => [...orders].sort((left, right) => new Date(right.created_at).getTime() - new Date(left.created_at).getTime()),
    [orders],
  );
  const visible = expanded ? sorted : sorted.slice(0, WORK_ORDER_PAGE);
  const inWindow = sorted.filter((order) => { const at = new Date(order.created_at).getTime(); return at >= start && at <= end; }).length;

  if (!sorted.length) {
    return <Panel title="Work-order history" className="work-order-panel"><div className="empty-state empty-state--compact"><Wrench size={18} aria-hidden="true" /><strong>No maintenance history</strong><span>This asset has no recorded work orders in the ERP extract.</span></div></Panel>;
  }

  return (
    <Panel
      title="Work-order history"
      className="work-order-panel"
      action={<span className="muted">{sorted.length} orders · {inWindow} in the plotted window</span>}
    >
      <ol className="work-order-list">
        {visible.map((order) => {
          const at = new Date(order.created_at).getTime();
          const highlighted = at >= start && at <= end;
          return (
            <li className={`work-order-row${highlighted ? " work-order-row--in-window" : ""}`} key={order.workorder_id}>
              <span className="work-order-when"><MonoNumber>{formatTimestamp(at)}</MonoNumber>{highlighted && <em>in view</em>}</span>
              <span className="work-order-code"><MonoNumber>{order.fault_code}</MonoNumber></span>
              <span className="work-order-description">{order.description}</span>
              <span className={`erp-order-status erp-order-status--${order.status}`}>{order.status.replaceAll("_", " ")}</span>
            </li>
          );
        })}
      </ol>
      {sorted.length > WORK_ORDER_PAGE && (
        <button className="quiet-button work-order-more" type="button" aria-expanded={expanded} onClick={() => setExpanded((open) => !open)}>
          {expanded ? "Show recent only" : `Show all ${sorted.length} work orders`}
        </button>
      )}
    </Panel>
  );
}

function MachineSchematic({ components, selectedName, onSelect }: { components: ComponentStatus[]; selectedName: string | null; onSelect: (name: string) => void }) {
  const tokens = useChartTokens();
  return (
    <Panel title="Machine schematic" className="schematic-panel" action={selectedName ? <button className="schematic-filter-reset" type="button" onClick={() => onSelect("")}>Show all</button> : undefined}>
      <div className="schematic-caption"><span className="eyebrow">Component risk map</span><span className="muted">Click a component to filter telemetry</span></div>
      <svg className="machine-schematic" viewBox="0 0 360 250" role="img" aria-labelledby="machine-schematic-title machine-schematic-description">
        <title id="machine-schematic-title">Interactive machine component schematic</title>
        <desc id="machine-schematic-description">Five named machine components are coloured by their risk band. Predicted fault components pulse and include a leader line.</desc>
        <path className="schematic-frame" d="M20 42H340V218H20Z" aria-hidden="true" />
        <path className="schematic-centreline" d="M28 135H332" aria-hidden="true" />
        {SCHEMATIC_LAYOUT.map((layout, index) => {
          const component = components[index];
          const slotLabel = component ? titleCase(component.name) : "Unmapped";
          const status: Band = component?.status ?? "NODATA";
          const selected = Boolean(component && selectedName === component.name);
          const predicted = component?.predicted ?? false;
          const color = statusFill(tokens, status);
          const label = component ? `${slotLabel}, ${Math.round(component.risk)} risk, ${status.toLowerCase()}${predicted ? ", predicted fault" : ""}` : `${slotLabel}, no data`;
          return (
            <g className={`schematic-component${selected ? " schematic-component--selected" : ""}${predicted ? " schematic-component--predicted" : ""}${!component ? " schematic-component--nodata" : ""}`} key={component?.name ?? index} data-band={status} role="button" tabIndex={component ? 0 : -1} aria-label={label} onClick={() => component && onSelect(selected ? "" : component.name)} onKeyDown={(event) => { if (component && (event.key === "Enter" || event.key === " ")) { event.preventDefault(); onSelect(selected ? "" : component.name); } }}>
              <path d={layout.path} data-component={component?.name ?? `slot-${index}`} fill={color} stroke={color} strokeWidth="2" fillRule="evenodd" />
              <text className="schematic-label" x={layout.labelX} y={layout.labelY} textAnchor="middle">{slotLabel}</text>
              <text className="schematic-status" x={layout.labelX} y={layout.labelY + 12} textAnchor="middle">{component ? `${status} · ${Math.round(component.risk)}` : "NO DATA"}</text>
              {predicted && <><path className="schematic-leader" d="M58 111L34 78H118" aria-hidden="true" /><text className="schematic-predicted-label" x="122" y="81">PREDICTED FAULT</text></>}
            </g>
          );
        })}
      </svg>
      <div className="schematic-legend" aria-label="Risk band legend">{(["LOW", "MEDIUM", "HIGH", "CRITICAL"] as Band[]).map((band) => <span key={band}><i style={{ background: statusFill(tokens, band) }} />{band}</span>)}</div>
      <p className="schematic-selection" aria-live="polite">{selectedName ? `Telemetry filtered to ${selectedName}.` : "All sensor channels shown."}</p>
    </Panel>
  );
}

function supportsWebGL() {
  if (typeof document === "undefined") return false;
  try {
    const canvas = document.createElement("canvas");
    return Boolean(canvas.getContext("webgl2") || canvas.getContext("webgl") || canvas.getContext("experimental-webgl"));
  } catch {
    return false;
  }
}

class DigitalTwinBoundary extends Component<{ children: ReactNode; fallback: ReactNode }, { failed: boolean }> {
  state = { failed: false };

  static getDerivedStateFromError() {
    return { failed: true };
  }

  render() {
    return this.state.failed ? this.props.fallback : this.props.children;
  }
}

function DigitalTwinLoading() {
  return <Panel title="3D digital twin" className="digital-twin-panel"><div className="digital-twin-loading"><Skeleton width="100%" height="270px" /></div></Panel>;
}

function DigitalTwinSlot({ components, machineType, selectedName, onSelect }: { components: ComponentStatus[]; machineType: string; selectedName: string | null; onSelect: (name: string) => void }) {
  const meta = useQuery({ queryKey: ["meta"], queryFn: () => fetchJson<MetaResponse>("/meta"), staleTime: 60_000 });
  const [webglAvailable, setWebglAvailable] = useState<boolean | null>(null);
  useEffect(() => setWebglAvailable(supportsWebGL()), []);

  const fallback = <MachineSchematic components={components} selectedName={selectedName} onSelect={onSelect} />;
  if (meta.isPending || meta.data?.feature_flags.digital_twin !== true || webglAvailable !== true) return fallback;
  return <DigitalTwinBoundary fallback={fallback}><Suspense fallback={<DigitalTwinLoading />}><DigitalTwin components={components} machineType={machineType} selectedName={selectedName} onSelect={onSelect} /></Suspense></DigitalTwinBoundary>;
}

function MachineDetailSkeleton() {
  return <div className="screen machine-detail-screen"><ScreenHeader kicker="Asset observability" title="Machine Detail" description="Loading synchronized telemetry and maintenance evidence." /><div className="detail-summary"><Skeleton width="180px" height="24px" /><Skeleton width="120px" height="24px" /><Skeleton width="140px" height="24px" /></div><div className="channel-grid">{DETAIL_CHANNELS.map((channel) => <Panel className="channel-panel channel-panel--skeleton" key={channel}><Skeleton width="42%" height="14px" /><Skeleton width="100%" height="164px" /></Panel>)}</div></div>;
}

function TelemetrySkeleton() {
  return <div className="channel-grid">{DETAIL_CHANNELS.map((channel) => <Panel className="channel-panel channel-panel--skeleton" key={channel}><Skeleton width="42%" height="14px" /><Skeleton width="100%" height="164px" /></Panel>)}</div>;
}

function FallbackPredictorBanner({ predictor }: { predictor: string | undefined }) {
  if (predictor !== "rule_fallback") return null;
  return <div className="fallback-predictor-banner" role="status" aria-live="polite"><AlertTriangle size={16} aria-hidden="true" /><span><strong>Fallback predictor active</strong> — accuracy not guaranteed</span></div>;
}

export function MachineDetailScreen() {
  const { machineId = "PUMP-07" } = useParams<{ machineId: string }>();
  const { user } = useAuth();
  const [selectedComponentName, setSelectedComponentName] = useState<string | null>(null);
  useEffect(() => setSelectedComponentName(null), [machineId]);
  const detail = useQuery({ queryKey: ["machine-detail", machineId], queryFn: () => fetchJson<MachineDetail>(`/machines/${machineId}`), staleTime: 30_000 });
  const prediction = useQuery({ queryKey: ["machine-prediction", machineId], queryFn: () => fetchJson<PredictionResponse>(`/machines/${machineId}/prediction`), staleTime: 30_000 });
  const timeWindow = detail.data ? eventWindow(detail.data) : null;
  const telemetry = useQuery({
    queryKey: ["machine-telemetry", machineId, timeWindow?.start, timeWindow?.end],
    enabled: Boolean(timeWindow),
    queryFn: () => fetchJson<{ series: TelemetryPoint[] }>(`/machines/${machineId}/telemetry?from=${encodeURIComponent(new Date(timeWindow!.start).toISOString())}&to=${encodeURIComponent(new Date(timeWindow!.end).toISOString())}&interval=60&channels=${DETAIL_CHANNELS.join(",")}`),
    staleTime: 30_000,
  });
  const points = useMemo(() => normalizePoints(telemetry.data?.series ?? [], DETAIL_CHANNELS), [telemetry.data]);
  const plottedPoints = useMemo(() => downsample(points, DETAIL_CHANNELS[0]), [points]);
  const start = timeWindow?.start ?? 0;
  const end = timeWindow?.end ?? 1;
  const segments = useMemo(() => qualitySegments(plottedPoints, start, end), [plottedPoints, start, end]);
  const visibleChannels = useMemo(() => {
    const selected = detail.data?.components.find((component) => component.name === selectedComponentName);
    return selected ? [selected.channel] : [...DETAIL_CHANNELS];
  }, [detail.data?.components, selectedComponentName]);

  if (detail.isPending) return <MachineDetailSkeleton />;
  if (detail.isError || !detail.data) return <div className="screen"><ScreenHeader kicker="Asset observability" title="Machine Detail" description="The selected machine could not be loaded." /><div className="empty-state"><AlertTriangle size={20} aria-hidden="true" /><strong>Machine detail unavailable</strong><span>Check the API connection and return to the Live Floor.</span></div></div>;

  const machine = detail.data;
  return (
    <div className="screen machine-detail-screen">
      <FallbackPredictorBanner predictor={prediction.data?.predictor} />
      <motion.div className="machine-detail-morph" layoutId={`machine-${machine.id}`}>
        <ScreenHeader kicker={`Asset observability / ${machine.id}`} title={machine.name} description={`${machine.type} · ${machine.line} · synchronized 24-hour telemetry view`} />
        <div className="detail-summary" aria-label="Machine summary"><span><b className="machine-id">{machine.id}</b><small>asset identifier</small></span><span><MonoNumber>{Math.round(machine.risk)}</MonoNumber><small>risk / 100</small></span><span><MonoNumber>{Math.round(machine.quality * 100)}%</MonoNumber><small>telemetry quality</small></span><span><MonoNumber>{machine.rated_rpm.toLocaleString()}</MonoNumber><small>rated rpm</small></span></div>
        {user?.role !== "operator" && <Link className="primary-button detail-diagnosis-link" to={`/diagnosis/new-${machine.id.toLowerCase()}`}><Wrench size={15} aria-hidden="true" />Explain current risk</Link>}
      </motion.div>
      <div className="machine-detail-layout">
        <div className="machine-detail-main">
          {telemetry.isPending ? <TelemetrySkeleton /> : telemetry.isError ? <div className="honest-state detail-notice"><Radio size={16} aria-hidden="true" /><div><strong>Telemetry unavailable</strong><p>Machine metadata and maintenance history remain available while the sensor service reconnects.</p></div></div> : !plottedPoints.length ? <div className="empty-state detail-notice"><Radio size={16} aria-hidden="true" /><strong>No telemetry in this window</strong><span>The machine metadata is available, but no sensor samples were returned for the selected 24-hour range.</span></div> : <>
            <QualityRibbon segments={segments} start={start} end={end} />
            {selectedComponentName && <div className="schematic-filter-notice"><Radio size={13} aria-hidden="true" />Showing {visibleChannels.map((channel) => CHANNEL_LABELS[channel] ?? channel).join(", ")} for {selectedComponentName}. <button type="button" onClick={() => setSelectedComponentName(null)}>Show all channels</button></div>}
            <div className="channel-grid">{visibleChannels.map((channel) => { return <ChannelChart key={channel} channel={channel} points={plottedPoints} events={machine.fault_events} segments={segments} start={start} end={end} />; })}</div>
            <WorkOrderRail orders={machine.history} start={start} end={end} />
          </>}
        </div>
        <aside className="machine-detail-side"><DigitalTwinSlot components={machine.components} machineType={machine.type} selectedName={selectedComponentName} onSelect={(name) => setSelectedComponentName(name || null)} /></aside>
      </div>
    </div>
  );
}

function GeneratedChip({ generated }: { generated: boolean }) {
  return <span className={`generated-chip${generated ? "" : " generated-chip--template"}`}><span className="generated-chip-dot" aria-hidden="true" />{generated ? "AI generated" : "Template fallback"}</span>;
}

function newDiagnosisMachineId(diagnosisId: string) {
  return diagnosisId.toLowerCase().startsWith("new-") ? diagnosisId.slice(4).toUpperCase() : "";
}

function CitationChip({ citation, onActivate }: { citation: Citation; onActivate: (citation: Citation) => void }) {
  return (
    <button
      type="button"
      className={`citation-chip citation-chip--${citation.kind === "sensor" ? "sensor" : "work-order"}`}
      onMouseEnter={() => onActivate(citation)}
      onFocus={() => onActivate(citation)}
      title={`${citation.id}: ${citation.title}`}
      aria-label={`${citation.id}, ${citation.title}`}
    >
      [{citation.id}]
    </button>
  );
}

function CitedText({ text, citations, onActivate }: { text: string; citations: Map<string, Citation>; onActivate: (citation: Citation) => void }) {
  const parts = text.split(/(\[[SM]\d+\])/g);
  return <>{parts.map((part, index) => { const match = part.match(/^\[([SM]\d+)\]$/); const citation = match ? citations.get(match[1]) : undefined; return citation ? <CitationChip key={`${citation.id}-${index}`} citation={citation} onActivate={onActivate} /> : <span key={`${part}-${index}`}>{part}</span>; })}</>;
}

function ConfidenceGauge({ confidence }: { confidence: number }) {
  const value = clamp(confidence, 0, 1);
  const angle = -90 + value * 180;
  return (
    <div className="confidence-gauge" role="img" aria-label={`Confidence ${(value * 100).toFixed(2)} percent`}>
      <div className="confidence-dial"><span className="confidence-needle" style={{ transform: `rotate(${angle}deg)` }} /><span className="confidence-centre" /></div>
      <div className="confidence-value"><MonoNumber>{value.toFixed(2)}</MonoNumber><span>confidence</span></div>
      <div className="confidence-scale"><span>0</span><span>0.5</span><span>1.0</span></div>
    </div>
  );
}

const RISK_TERM_LABELS: Record<string, string> = {
  p_failure_24h: "Failure probability",
  rul_pressure: "RUL pressure",
  history_prior: "Maintenance history",
  criticality_weight: "Asset criticality",
  data_quality: "Data quality",
};

function RiskWaterfall({ risk, terms }: { risk: number; terms: Record<string, number> }) {
  const entries = Object.entries(terms);
  const adjusted = entries.map(([name, contribution], index) => [name, index === entries.length - 1 ? contribution + (risk - entries.reduce((sum, [, value]) => sum + value, 0)) : contribution] as const);
  return (
    <Panel title="Risk composition" className="risk-waterfall-panel">
      <div className="risk-waterfall-total"><span className="eyebrow">Fused risk</span><span><MonoNumber>{risk.toFixed(2)}</MonoNumber><small>/ 100</small></span></div>
      <div className="risk-waterfall" aria-label="Five-term risk waterfall">
        {adjusted.map(([name, contribution]) => <div className="risk-term" key={name}><div className="risk-term-label"><span>{RISK_TERM_LABELS[name] ?? name.replaceAll("_", " ")}</span><MonoNumber>{contribution.toFixed(2)}</MonoNumber></div><div className="risk-term-track"><span style={{ width: `${risk ? clamp((Math.max(0, contribution) / risk) * 100, 0, 100) : 0}%` }} /></div></div>)}
      </div>
    </Panel>
  );
}

function ActionCard({ action, citations, onActivate }: { action: RecommendedAction; citations: Map<string, Citation>; onActivate: (citation: Citation) => void }) {
  return (
    <article className="recommended-action">
      <div className="recommended-action-header"><div><span className="eyebrow">Recommended action</span><h3>{action.action}</h3></div><span className={`priority-badge priority-badge--${action.priority}`}>{action.priority}</span></div>
      <p><CitedText text={action.rationale} citations={citations} onActivate={onActivate} /></p>
      <div className="action-facts"><span><Wrench size={14} aria-hidden="true" /><b>Parts</b>{action.parts.join(" · ") || "None specified"}</span><span><Gauge size={14} aria-hidden="true" /><b>Labour</b>{action.est_labour_hours.toFixed(1)} h</span><span><Radio size={14} aria-hidden="true" /><b>Window</b>{action.window}</span></div>
    </article>
  );
}

function WorkOrderEvidenceCard({ citation }: { citation: Citation }) {
  return (
    <aside className="work-order-evidence" aria-live="polite">
      <div className="work-order-evidence-header"><span className="eyebrow">Work-order evidence</span><span className="citation-chip citation-chip--work-order">[{citation.id}]</span></div>
      <h3>{citation.title}</h3>
      <div className="work-order-evidence-grid"><span><small>Date</small><MonoNumber>{citation.date ? formatTimestamp(citation.date) : "Unknown"}</MonoNumber></span><span><small>Fault code</small><MonoNumber>{citation.fault_code ?? "Unknown"}</MonoNumber></span><span><small>Cost</small><MonoNumber>{citation.cost_inr == null ? "Unknown" : formatInr(citation.cost_inr, 2)}</MonoNumber></span></div>
      <p>{citation.technician_note ?? citation.detail}</p>
    </aside>
  );
}

function DiagnosisSensorEvidence({ points, channel, start, end, active }: { points: DetailPoint[]; channel: string; start: number; end: number; active: Citation | null }) {
  const tokens = useChartTokens();
  return (
    <Panel title="Sensor evidence" className="diagnosis-sensor-panel">
      <div className="diagnosis-sensor-caption"><span>{CHANNEL_LABELS[channel] ?? channel} · {CHANNEL_UNITS[channel] ?? "value"}</span><span className="muted">Hover [S#] to shade its exact window</span></div>
      <div className="diagnosis-chart-container" id="diagnosis-sensor-chart"><ResponsiveContainer width="100%" height={220}><LineChart data={points} margin={{ top: 14, right: 16, bottom: 0, left: 0 }}>
        <CartesianGrid stroke={tokens.grid} strokeDasharray="2 3" vertical={false} />
        <XAxis dataKey="ts" type="number" domain={[start, end]} tickFormatter={(value) => formatTimestamp(value)} tick={{ fill: tokens.tick, fontSize: 10 }} tickLine={false} axisLine={{ stroke: tokens.axis }} minTickGap={48} />
        <YAxis tick={{ fill: tokens.tick, fontSize: 10 }} tickLine={false} axisLine={false} width={44} domain={["auto", "auto"]} />
        <Tooltip labelFormatter={(value) => formatTimestamp(Number(value))} contentStyle={{ background: tokens.tooltipBg, border: `1px solid ${tokens.border}`, borderRadius: 6, color: tokens.text, fontSize: 11, boxShadow: "0 2px 10px rgba(0,0,0,.18)" }} cursor={{ stroke: tokens.axis, strokeWidth: 1 }} />
        {active?.t0 && active.t1 && <ReferenceArea x1={new Date(active.t0).getTime()} x2={new Date(active.t1).getTime()} fill={tokens.accent} fillOpacity={0.16} ifOverflow="extendDomain" label={{ value: `[${active.id}]`, fill: tokens.accent, fontSize: 10, position: "insideTop" }} />}
        <Line type="monotone" dataKey={channel} name={CHANNEL_LABELS[channel] ?? channel} stroke={tokens.series} strokeWidth={2} dot={false} isAnimationActive={false} connectNulls={false} />
      </LineChart></ResponsiveContainer></div>
      {active?.kind === "sensor" && <div className="active-citation-note"><Info size={14} aria-hidden="true" />[{active.id}] {active.title}: {active.t0 && formatTimestamp(active.t0)} → {active.t1 && formatTimestamp(active.t1)}</div>}
    </Panel>
  );
}

function DiagnosisBlock({ kind, title, icon: Icon, items, generated, citations, onActivate }: { kind: "facts" | "assumptions" | "limits"; title: string; icon: typeof CheckCircle2; items: string[]; generated: boolean; citations: Map<string, Citation>; onActivate: (citation: Citation) => void }) {
  return (
    <section className={`diagnosis-block diagnosis-block--${kind}`}>
      <div className="diagnosis-block-header"><div className="diagnosis-block-title"><Icon size={17} aria-hidden="true" /><h2>{title}</h2></div><GeneratedChip generated={generated} /></div>
      {items.length ? <ul>{items.map((item, index) => <li key={`${kind}-${index}`}><CitedText text={item} citations={citations} onActivate={onActivate} /></li>)}</ul> : <p className="muted">No {kind} were returned.</p>}
    </section>
  );
}

function DiagnosisSkeleton() {
  return <div className="screen diagnosis-screen"><ScreenHeader kicker="Evidence-led diagnosis" title="Diagnosis" description="Loading grounded diagnosis and evidence." /><div className="diagnosis-hero"><Panel><Skeleton width="60%" height="18px" /><Skeleton width="42%" height="38px" /><Skeleton width="100%" height="64px" /></Panel><Panel><Skeleton width="100%" height="190px" /></Panel></div><div className="diagnosis-block-grid"><Panel><Skeleton width="90%" height="90px" /></Panel><Panel><Skeleton width="90%" height="90px" /></Panel><Panel><Skeleton width="90%" height="90px" /></Panel></div></div>;
}

export function DiagnosisScreen() {
  const { diagnosisId = "missing" } = useParams<{ diagnosisId: string }>();
  const navigate = useNavigate();
  const requestedMachineId = newDiagnosisMachineId(diagnosisId);
  const creating = Boolean(requestedMachineId);
  const diagnosisRun = useQuery({
    queryKey: ["diagnosis-resource", diagnosisId],
    queryFn: async () => {
      if (creating) return fetchJson<{ trace_id: string; diagnosis: DiagnosisResponse }>(`/machines/${requestedMachineId}/diagnose`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ force_refresh: false }) });
      const diagnosis = await fetchJson<DiagnosisResponse>(`/diagnoses/${diagnosisId}`);
      return { trace_id: diagnosis.trace_id ?? "", diagnosis };
    },
    staleTime: 30_000,
  });
  useEffect(() => {
    if (creating && diagnosisRun.data?.diagnosis.diagnosis_id) navigate(`/diagnosis/${diagnosisRun.data.diagnosis.diagnosis_id}`, { replace: true });
  }, [creating, diagnosisRun.data?.diagnosis.diagnosis_id, navigate]);
  const diagnosis = diagnosisRun.data?.diagnosis;
  const machineId = diagnosis?.machine_id ?? requestedMachineId;
  const traceId = diagnosis?.trace_id ?? diagnosisRun.data?.trace_id;
  const trace = useQuery({ queryKey: ["agent-trace", traceId], enabled: Boolean(traceId), queryFn: () => fetchJson<RunResponse>(`/runs/${traceId}`), staleTime: 5_000, refetchInterval: (query) => query.state.data?.status === "running" ? 1_000 : false });
  const citations = useMemo(() => new Map((diagnosis?.citations ?? []).map((citation) => [citation.id, citation])), [diagnosis]);
  const sensorCitations = useMemo(() => (diagnosis?.citations ?? []).filter((citation) => citation.kind === "sensor" && citation.t0 && citation.t1), [diagnosis]);
  const sensorStart = sensorCitations.length ? Math.min(...sensorCitations.map((citation) => new Date(citation.t0!).getTime())) : Date.now() - 6 * 60 * 60 * 1000;
  const sensorEnd = sensorCitations.length ? Math.max(...sensorCitations.map((citation) => new Date(citation.t1!).getTime())) : Date.now();
  const [activeCitation, setActiveCitation] = useState<Citation | null>(null);
  const [activeWorkOrder, setActiveWorkOrder] = useState<Citation | null>(null);
  const [evidenceExpanded, setEvidenceExpanded] = useState(false);
  const sensor = useQuery({ queryKey: ["diagnosis-sensor", machineId, sensorStart, sensorEnd], enabled: Boolean(diagnosis), queryFn: () => fetchJson<TelemetryResponse>(`/machines/${machineId}/telemetry?from=${encodeURIComponent(new Date(sensorStart).toISOString())}&to=${encodeURIComponent(new Date(sensorEnd).toISOString())}&interval=60&channels=vibration_rms_mm_s,temp_bearing_c`), staleTime: 30_000 });
  const sensorChannel = activeCitation?.channel ?? sensorCitations[0]?.channel ?? "vibration_rms_mm_s";
  const sensorPoints = useMemo(() => downsample(normalizePoints(sensor.data?.series ?? [], ["vibration_rms_mm_s", "temp_bearing_c"]), sensorChannel), [sensor.data, sensorChannel]);
  const activateCitation = (citation: Citation) => {
    setEvidenceExpanded(true);
    if (citation.kind === "sensor") {
      setActiveCitation(citation);
      setActiveWorkOrder(null);
      window.setTimeout(() => document.getElementById("diagnosis-sensor-chart")?.scrollIntoView({ behavior: "smooth", block: "nearest" }), 0);
    } else {
      setActiveWorkOrder(citation);
      setActiveCitation(null);
    }
  };

  if (diagnosisRun.isPending) return <DiagnosisSkeleton />;
  if (diagnosisRun.isError || !diagnosis) return <div className="screen"><ScreenHeader kicker="Evidence-led diagnosis" title="Diagnosis unavailable" description="The diagnosis service did not return a grounded result." /><div className="honest-state"><CircleAlert size={20} aria-hidden="true" /><div><strong>Unable to load diagnosis</strong><p>Machine telemetry remains available. Retry from the alert feed when the diagnosis service reconnects.</p></div></div></div>;

  const actionItems = diagnosis.recommended_actions.length ? diagnosis.recommended_actions : diagnosis.actions.map((action) => ({ action, priority: "medium" as const, rationale: action, parts: [], est_labour_hours: 0, window: "Review with planner", citations: [] }));
  return (
    <div className="screen diagnosis-screen">
      <ScreenHeader kicker={`Diagnosis / ${diagnosis.machine_id}`} title="Risk explanation" description="Start with the explanation and recommended action. Open the evidence only when you need to verify the details." />
      {!diagnosis.generated && <div className="offline-diagnosis-banner" role="status"><CircleAlert size={16} aria-hidden="true" /><div><strong>{diagnosis.offline_mode ? "Offline diagnosis mode" : "LLM fallback active"}</strong><span>{diagnosis.offline_mode ? "The gateway is bypassed; this result uses the deterministic template." : "The live response did not pass validation, so a grounded deterministic explanation is shown instead."}</span></div></div>}
      <div className="diagnosis-hero">
        <Panel className="diagnosis-fault-panel diagnosis-explanation-panel"><div className="diagnosis-fault-kicker"><span className="fault-mode-marker" aria-hidden="true" /><span className="eyebrow">{diagnosis.generated ? "LLM-GENERATED EXPLANATION" : "DETERMINISTIC EXPLANATION"}</span><GeneratedChip generated={diagnosis.generated} /></div><h2>{diagnosis.fault_mode.replaceAll("_", " ")}</h2><p className="diagnosis-explanation"><CitedText text={diagnosis.explanation} citations={citations} onActivate={activateCitation} /></p><div className="diagnosis-status-row"><span className="diagnosis-status"><CheckCircle2 size={15} aria-hidden="true" /> Evidence linked</span><span className="muted">{diagnosis.citations.length} sources · risk {Math.round(diagnosis.risk)}/100</span></div></Panel>
        <Panel title="Confidence" className="confidence-panel"><ConfidenceGauge confidence={diagnosis.confidence} /></Panel>
      </div>
      <section className="actions-panel diagnosis-next-action"><div className="section-heading"><div><span className="eyebrow">What to do next</span><h2>Recommended action</h2></div><GeneratedChip generated={diagnosis.generated} /></div><div className="action-list">{actionItems.map((action, index) => <ActionCard action={action} citations={citations} onActivate={activateCitation} key={`${action.action}-${index}`} />)}</div></section>
      <details className="diagnosis-disclosure" open={evidenceExpanded} onToggle={(event) => setEvidenceExpanded(event.currentTarget.open)}>
        <summary><span><strong>Evidence and reasoning</strong><small>Review citations, assumptions, limits, and risk calculation</small></span></summary>
        <div className="diagnosis-disclosure-body">
          <div className="diagnosis-content-grid">
            <div className="diagnosis-main-column">
              <section className="reasoning-panel"><div className="section-heading"><div><span className="eyebrow">Why this conclusion</span><h2>Reasoning steps</h2></div><GeneratedChip generated={diagnosis.generated} /></div><ol className="reasoning-steps">{diagnosis.reasoning.map((step, index) => <li key={`${step}-${index}`}><span className="reasoning-index">{String(index + 1).padStart(2, "0")}</span><p><CitedText text={step} citations={citations} onActivate={activateCitation} /></p></li>)}</ol></section>
              <div className="diagnosis-block-grid"><DiagnosisBlock kind="facts" title="Facts" icon={CheckCircle2} items={diagnosis.facts} generated={diagnosis.generated} citations={citations} onActivate={activateCitation} /><DiagnosisBlock kind="assumptions" title="Assumptions" icon={Info} items={diagnosis.assumptions} generated={diagnosis.generated} citations={citations} onActivate={activateCitation} /><DiagnosisBlock kind="limits" title="Limits" icon={CircleAlert} items={diagnosis.limits} generated={diagnosis.generated} citations={citations} onActivate={activateCitation} /></div>
            </div>
            <aside className="diagnosis-side-column"><RiskWaterfall risk={diagnosis.risk} terms={diagnosis.risk_terms} />{activeWorkOrder ? <WorkOrderEvidenceCard citation={activeWorkOrder} /> : <div className="citation-hint"><Info size={16} aria-hidden="true" /><span>Hover a <b>[M#]</b> citation to inspect the cited work order.</span></div>}</aside>
          </div>
          <DiagnosisSensorEvidence points={sensorPoints} channel={sensorChannel} start={sensorStart} end={sensorEnd} active={activeCitation ?? sensorCitations[0] ?? null} />
          {sensor.isError && <div className="honest-state detail-notice"><Radio size={16} aria-hidden="true" /><div><strong>Sensor chart unavailable</strong><p>The diagnosis and cited work-order evidence remain available while telemetry reconnects.</p></div></div>}
        </div>
      </details>
      <details className="diagnosis-disclosure diagnosis-technical-disclosure">
        <summary><span><strong>Technical trace and provenance</strong><small>{diagnosis.provider} · {diagnosis.model} · prompt {diagnosis.prompt_version}</small></span></summary>
        <div className="diagnosis-disclosure-body"><div className="diagnosis-meta"><span className="machine-id">{diagnosis.machine_id}</span><span>prediction {diagnosis.prediction_id}</span><span>model <MonoNumber>{diagnosis.model}</MonoNumber></span><span>provider <MonoNumber>{diagnosis.provider}</MonoNumber></span><GeneratedChip generated={diagnosis.generated} /></div>{trace.isPending ? <AgentTraceLoading /> : trace.isError || !trace.data ? <div className="honest-state trace-notice"><CircleAlert size={18} aria-hidden="true" /><div><strong>Agent trace unavailable</strong><p>The diagnosis is available, but persisted stage timings could not be loaded. No timings are inferred.</p></div></div> : <AgentTraceTimeline run={trace.data} />}</div>
      </details>
    </div>
  );
}

type UserRole = "operator" | "technician" | "planner" | "admin";
type ScheduleScreenProps = { currentRole?: UserRole };

const CALENDAR_DAYS = 14;
const DAY_MS = 24 * 60 * 60 * 1000;

function startOfDay(timestamp: number) {
  const date = new Date(timestamp);
  return Date.UTC(date.getUTCFullYear(), date.getUTCMonth(), date.getUTCDate());
}

function calendarDayKey(timestamp: number | string) {
  return new Date(timestamp).toISOString().slice(0, 10);
}

function formatCalendarDay(timestamp: number) {
  return new Intl.DateTimeFormat("en-GB", { weekday: "short", day: "2-digit", month: "short" }).format(new Date(timestamp));
}

function calendarAnchor(windows: ScheduleWindow[]) {
  const firstWindow = windows.length ? Math.min(...windows.map((window) => new Date(window.start).getTime())) : Date.now();
  return startOfDay(firstWindow);
}

function autonomyDescription(approval: Approval) {
  return `${approval.autonomy_level} · ${approval.required_role} ${approval.decision_action}`;
}

function ApprovalEvidenceDrawer({
  approval,
  window,
  machine,
  prediction,
  diagnosis,
  evidencePending,
  evidenceError,
  canDecide,
  rejecting,
  approving,
  rejectReason,
  rejectError,
  onClose,
  onApprove,
  onReject,
  onRejectReasonChange,
}: {
  approval: Approval;
  window: ScheduleWindow | undefined;
  machine: MachineDetail | undefined;
  prediction: PredictionResponse | undefined;
  diagnosis: DiagnosisResponse | undefined;
  evidencePending: boolean;
  evidenceError: boolean;
  canDecide: boolean;
  rejecting: boolean;
  approving: boolean;
  rejectReason: string;
  rejectError: string | null;
  onClose: () => void;
  onApprove: () => void;
  onReject: (event: FormEvent<HTMLFormElement>) => void;
  onRejectReasonChange: (value: string) => void;
}) {
  const erpPayload = approval.action_payload;
  return (
    <div className="drawer-backdrop" role="presentation" onClick={onClose}>
      <aside className="evidence-drawer" role="dialog" aria-modal="true" aria-labelledby="evidence-drawer-title" onClick={(event) => event.stopPropagation()}>
        <div className="drawer-header"><div><span className="eyebrow">Approval evidence</span><h2 id="evidence-drawer-title">{approval.machine_id}</h2></div><button className="icon-button" type="button" onClick={onClose} aria-label="Close evidence drawer"><X size={18} aria-hidden="true" /></button></div>
        <div className="drawer-scroll">
          <div className="approval-summary"><span className={`autonomy-badge autonomy-badge--${approval.autonomy_level}`}><b>{approval.autonomy_level}</b>{approval.required_role}</span><span className="approval-state approval-state--pending">PENDING REVIEW</span><span className="muted">{window ? `${formatTimestamp(window.start)} → ${formatTimestamp(window.end)}` : "No schedule window returned"}</span></div>
          <section className="evidence-section"><div className="section-heading"><div><span className="eyebrow">Evidence bundle</span><h3>What supports this decision</h3></div><FileJson size={16} aria-hidden="true" /></div>
            {evidencePending ? <div className="drawer-skeleton"><Skeleton width="100%" height="14px" /><Skeleton width="85%" height="14px" /><Skeleton width="70%" height="14px" /></div> : evidenceError ? <div className="honest-state honest-state--compact"><CircleAlert size={16} aria-hidden="true" /><div><strong>Evidence partially unavailable</strong><p>The approval metadata is available, but one or more supporting API responses did not load.</p></div></div> : <div className="evidence-facts"><span><small>Predicted fault</small><b>{diagnosis?.fault_mode.replaceAll("_", " ") ?? window?.fault_mode ?? "Not returned"}</b></span><span><small>Failure probability / 24h</small><MonoNumber>{prediction ? `${Math.round(prediction.p_failure_24h * 100)}%` : "Not returned"}</MonoNumber></span><span><small>Confidence</small><MonoNumber>{prediction ? `${Math.round(prediction.confidence * 100)}%` : "Not returned"}</MonoNumber></span><span><small>Risk band</small><b>{prediction?.band ?? machine?.band ?? "NODATA"}</b></span></div>}
          </section>
          {approval.decision_action === "approve" ? <section className="evidence-section"><div className="section-heading"><div><span className="eyebrow">ERP write preview</span><h3>Immutable payload for review</h3></div><Wrench size={16} aria-hidden="true" /></div><p className="drawer-note">The server stored and hashed this payload when the approval was requested. Approval sends these exact values to ERP.</p><pre className="erp-payload"><code>{JSON.stringify(erpPayload, null, 2)}</code></pre></section> : <section className="evidence-section"><div className="section-heading"><div><span className="eyebrow">Technician acknowledgement</span><h3>No ERP write</h3></div><CheckCircle2 size={16} aria-hidden="true" /></div><p className="drawer-note">Acknowledgement records that the technician reviewed this recommendation. It does not create a work order.</p></section>}
          {canDecide ? <section className="drawer-actions"><button className="primary-button" type="button" onClick={onApprove} disabled={approving || rejecting || evidencePending || evidenceError || (approval.decision_action === "approve" && !erpPayload)}>{approving ? (approval.decision_action === "approve" ? "Approving…" : "Acknowledging…") : (approval.decision_action === "approve" ? "Approve and create work order" : "Acknowledge recommendation")}</button><form onSubmit={onReject}><label className="drawer-label" htmlFor="reject-reason">Reject reason <span>(required)</span></label><textarea id="reject-reason" value={rejectReason} onChange={(event) => onRejectReasonChange(event.target.value)} maxLength={500} rows={4} placeholder="Explain why this recommendation should not be scheduled." aria-describedby="reject-reason-help reject-reason-error" /><div id="reject-reason-help" className="drawer-form-help">{rejectReason.length}/500 characters</div>{rejectError && <div id="reject-reason-error" className="form-error" role="alert"><CircleAlert size={14} aria-hidden="true" />{rejectError}</div>}<button className="danger-outline-button" type="submit" disabled={approving || rejecting || !rejectReason.trim()}>{rejecting ? "Rejecting…" : "Reject with reason"}</button></form></section> : <div className="honest-state honest-state--compact"><UserRound size={16} aria-hidden="true" /><div><strong>{approval.required_role} approval required</strong><p>Your current role cannot decide this item. The API remains the enforcement boundary.</p></div></div>}
        </div>
      </aside>
    </div>
  );
}

export function ScheduleScreen({ currentRole = "planner" }: ScheduleScreenProps) {
  const queryClient = useQueryClient();
  const toast = useToast();
  const [selectedApprovalId, setSelectedApprovalId] = useState<string | null>(null);
  const [rejectReason, setRejectReason] = useState("");
  const [rejectError, setRejectError] = useState<string | null>(null);
  const schedule = useQuery({ queryKey: ["schedule", "14-day"], queryFn: () => fetchJson<ScheduleWindow[]>("/schedule"), staleTime: 30_000 });
  const approvals = useQuery({ queryKey: ["approvals", "pending"], queryFn: () => fetchJson<Approval[]>("/approvals?state=pending"), staleTime: 5_000, refetchInterval: 10_000 });
  const selectedApproval = approvals.data?.find((approval) => approval.id === selectedApprovalId);
  const selectedMachineId = selectedApproval?.machine_id;
  const selectedWindow = schedule.data?.find((window) => window.machine_id === selectedMachineId);
  const machine = useQuery({ queryKey: ["schedule-machine", selectedMachineId], enabled: Boolean(selectedMachineId), queryFn: () => fetchJson<MachineDetail>(`/machines/${selectedMachineId}`), staleTime: 30_000 });
  const prediction = useQuery({ queryKey: ["schedule-prediction", selectedMachineId], enabled: Boolean(selectedMachineId), queryFn: () => fetchJson<PredictionResponse>(`/machines/${selectedMachineId}/prediction`), staleTime: 30_000 });
  const diagnosis = useQuery({ queryKey: ["schedule-diagnosis", selectedMachineId], enabled: Boolean(selectedMachineId), queryFn: () => fetchJson<{ trace_id: string; diagnosis: DiagnosisResponse }>(`/machines/${selectedMachineId}/diagnose`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ force_refresh: false }) }), staleTime: 30_000 });
  const previousApprovals = useRef<Approval[] | undefined>(undefined);
  const decisionMutation = useMutation({
    mutationFn: ({ approvalId, decision, reason }: { approvalId: string; decision: "approve" | "acknowledge" | "reject"; reason?: string }) => fetchJson<Approval>(`/approvals/${approvalId}/${decision}`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(decision === "reject" ? { reason } : {}) }),
    onMutate: async ({ approvalId, decision, reason }) => {
      await queryClient.cancelQueries({ queryKey: ["approvals", "pending"] });
      previousApprovals.current = queryClient.getQueryData<Approval[]>(["approvals", "pending"]);
      queryClient.setQueryData<Approval[]>(["approvals", "pending"], (current) => current?.filter((approval) => approval.id !== approvalId));
      setSelectedApprovalId(null);
      setRejectReason("");
      setRejectError(null);
      toast.push(decision === "reject" ? "Recommendation rejected." : decision === "acknowledge" ? "Acknowledgement submitted." : "Approval submitted.", { tone: "info", detail: reason });
    },
    onError: (error) => {
      queryClient.setQueryData(["approvals", "pending"], previousApprovals.current);
      toast.push("Decision failed; the approval was restored.", { tone: "error", detail: error instanceof Error ? error.message : "Retry when the API reconnects." });
    },
    onSuccess: (approval, variables) => {
      if (variables.decision === "reject") {
        toast.push("Recommendation rejected.", { tone: "success", detail: "The decision is recorded in the audit ledger." });
      } else if (variables.decision === "acknowledge") {
        toast.push("Recommendation acknowledged.", { tone: "success", detail: "No ERP work order was created." });
      } else if (approval.erp_sync_status === "pending_sync") {
        toast.push("Approved — work order queued.", { tone: "warning", detail: "ERP is unavailable, so the work order is queued for automatic synchronization." });
      } else {
        toast.push("Approved and synced to ERP.", { tone: "success", detail: approval.erp_workorder_id ? `Work order ${approval.erp_workorder_id}` : undefined });
      }
      void queryClient.invalidateQueries({ queryKey: ["erp"] });
    },
    onSettled: () => { void queryClient.invalidateQueries({ queryKey: ["approvals", "pending"] }); },
  });
  const windows = schedule.data ?? [];
  const calendarStart = calendarAnchor(windows);
  const calendarDays = Array.from({ length: CALENDAR_DAYS }, (_, index) => calendarStart + index * DAY_MS);
  const selectedCanDecide = selectedApproval ? currentRole === selectedApproval.required_role || currentRole === "admin" : false;
  const evidencePending = machine.isPending || prediction.isPending || diagnosis.isPending;
  const evidenceError = machine.isError || prediction.isError || diagnosis.isError;

  const openApproval = (approval: Approval) => { setSelectedApprovalId(approval.id); setRejectError(null); setRejectReason(""); };
  const closeDrawer = () => { if (!decisionMutation.isPending) { setSelectedApprovalId(null); setRejectError(null); setRejectReason(""); } };
  const approveSelected = () => { if (selectedApproval && selectedCanDecide) decisionMutation.mutate({ approvalId: selectedApproval.id, decision: selectedApproval.decision_action }); };
  const rejectSelected = (event: FormEvent<HTMLFormElement>) => { event.preventDefault(); if (!selectedApproval || !selectedCanDecide) return; if (!rejectReason.trim()) { setRejectError("A rejection reason is required."); return; } decisionMutation.mutate({ approvalId: selectedApproval.id, decision: "reject", reason: rejectReason.trim() }); };

  return (
    <div className="screen schedule-screen">
      <ScreenHeader kicker="Maintenance planning" title="Schedule & Approvals" description="Review predicted maintenance windows, inspect the evidence, and make an accountable approval decision." />
      <div className="schedule-grid">
        <Panel title="14-day maintenance calendar" className="schedule-calendar-panel">
          <div className="calendar-toolbar"><span className="muted">Predicted windows are model recommendations; planned windows are committed work.</span><div className="calendar-legend"><span className="calendar-legend-item calendar-legend-item--predicted">Predicted</span><span className="calendar-legend-item calendar-legend-item--planned">Planned</span></div></div>
          {schedule.isPending ? <div className="calendar-grid calendar-grid--skeleton">{calendarDays.map((day) => <div className="calendar-day" key={day}><Skeleton width="78px" height="12px" /><Skeleton width="100%" height="54px" /></div>)}</div> : schedule.isError ? <div className="honest-state"><AlertTriangle size={20} aria-hidden="true" /><div><strong>Schedule unavailable</strong><p>Predicted windows could not be loaded. Approval decisions remain protected until the schedule service reconnects.</p></div></div> : <div className="calendar-grid">{calendarDays.map((day) => { const dayKey = calendarDayKey(day); const dayWindows = windows.filter((window) => dayKey >= calendarDayKey(window.start) && dayKey <= calendarDayKey(window.end)); return <div className="calendar-day" key={dayKey}><div className="calendar-day-label">{formatCalendarDay(day)}</div>{dayWindows.length ? dayWindows.map((window) => <button className={`calendar-window calendar-window--${window.kind}`} type="button" key={window.id} onClick={() => { const approval = approvals.data?.find((item) => item.machine_id === window.machine_id); if (approval) openApproval(approval); else toast.push(`No pending approval is attached to ${window.machine_id}.`, { tone: "info" }); }}><b>{window.machine_id}</b><span>{window.fault_mode.replaceAll("_", " ")}</span><small>{window.kind}</small></button>) : <span className="calendar-empty">No window</span>}</div>; })}</div>}
        </Panel>
        <Panel title="Approval queue" className="approval-queue-panel">
          {approvals.isPending ? <div className="approval-list">{Array.from({ length: 3 }, (_, index) => <div className="approval-card approval-card--skeleton" key={index}><Skeleton width="64px" height="20px" /><Skeleton width="90%" height="14px" /><Skeleton width="110px" height="36px" /></div>)}</div> : approvals.isError ? <div className="honest-state honest-state--compact"><AlertTriangle size={18} aria-hidden="true" /><div><strong>Approval queue unavailable</strong><p>Existing decisions are not changed while the API reconnects.</p></div></div> : approvals.data?.length ? <div className="approval-list">{approvals.data.map((approval) => <article className="approval-card" key={approval.id}><div className="approval-card-header"><span className={`autonomy-badge autonomy-badge--${approval.autonomy_level}`}><b>{approval.autonomy_level}</b>{approval.required_role}</span><span className="approval-state approval-state--pending">{approval.state}</span></div><h3>{approval.machine_id}</h3><p>{autonomyDescription(approval)}</p><div className="approval-card-footer"><span className="machine-id">{approval.prediction_id}</span>{currentRole === approval.required_role || currentRole === "admin" ? <button className="quiet-button" type="button" onClick={() => openApproval(approval)}>Review evidence</button> : <span className="required-role"><UserRound size={13} aria-hidden="true" />{approval.required_role} only</span>}</div></article>)}</div> : <div className="honest-state honest-state--compact"><CheckCircle2 size={18} aria-hidden="true" /><div><strong>No approvals waiting</strong><p>The queue is clear. New A0/A1 recommendations will appear here.</p></div></div>}
        </Panel>
      </div>
      {selectedApproval && <ApprovalEvidenceDrawer approval={selectedApproval} window={selectedWindow} machine={machine.data} prediction={prediction.data} diagnosis={diagnosis.data?.diagnosis} evidencePending={evidencePending} evidenceError={evidenceError} canDecide={selectedCanDecide} rejecting={decisionMutation.isPending && decisionMutation.variables?.decision === "reject"} approving={decisionMutation.isPending && decisionMutation.variables?.decision !== "reject"} rejectReason={rejectReason} rejectError={rejectError} onClose={closeDrawer} onApprove={approveSelected} onReject={rejectSelected} onRejectReasonChange={(value) => { setRejectReason(value); if (value.trim()) setRejectError(null); }} />}
    </div>
  );
}

function LedgerSkeleton() {
  return <div className="screen"><ScreenHeader kicker="Counterfactual downtime" title="Downtime Ledger" description="Loading policy comparison from the downtime simulator." /><div className="ledger-summary"><Skeleton width="180px" height="26px" /><Skeleton width="120px" height="26px" /></div><div className="ledger-policy-grid">{Array.from({ length: 3 }, (_, index) => <Panel className="ledger-policy-card" key={index}><Skeleton width="90px" height="14px" /><Skeleton width="150px" height="30px" /><Skeleton width="100%" height="10px" /></Panel>)}</div></div>;
}

export function LedgerScreen() {
  const tokens = useChartTokens();
  const [tau, setTau] = useState<number | null>(null);
  const [debouncedTau, setDebouncedTau] = useState<number | null>(null);
  const [showMissed, setShowMissed] = useState(false);
  const reducedMotion = useReducedMotion() ?? false;
  const ledger = useQuery({
    queryKey: ["ledger", debouncedTau],
    queryFn: () => fetchJson<LedgerResponse>(debouncedTau === null ? "/ledger" : `/ledger?tau=${debouncedTau.toFixed(2)}`),
    placeholderData: keepPreviousData,
    staleTime: 30_000,
  });

  useEffect(() => {
    const timeout = window.setTimeout(() => setDebouncedTau(tau), tau === null ? 0 : 45);
    return () => window.clearTimeout(timeout);
  }, [tau]);

  const reduction = useCountUp(ledger.data?.reduction_pct ?? 0, reducedMotion);
  const predictivePolicy = ledger.data?.policies.find((policy) => policy.policy === "predictive");
  const avoidedCost = predictivePolicy?.avoided_cost_inr ?? 0;
  if (ledger.isPending && !ledger.data) return <LedgerSkeleton />;
  if (ledger.isError && !ledger.data) return <div className="screen"><ScreenHeader kicker="Counterfactual downtime" title="Downtime Ledger unavailable" description="The policy simulator did not return a current comparison." /><div className="honest-state"><CircleAlert size={20} aria-hidden="true" /><div><strong>Ledger data unavailable</strong><p>The rest of the control room remains available while the simulator reconnects.</p></div></div></div>;
  if (!ledger.data) return <LedgerSkeleton />;

  const policies = ledger.data.policies;
  const maximumDowntime = Math.max(...policies.map((policy) => policy.downtime_minutes), 1);
  const reactiveDowntime = policies.find((policy) => policy.policy === "reactive")?.downtime_minutes ?? maximumDowntime;
  const targetPosition = clamp((reactiveDowntime * (1 - ledger.data.target_reduction_pct / 100) / maximumDowntime) * 100, 0, 100);
  const effectiveTau = tau ?? ledger.data.tau;
  const missedFailures = ledger.data.missed_failures ?? [];

  return (
    <div className="screen ledger-screen">
      <ScreenHeader kicker="Counterfactual downtime" title="Downtime Ledger" description="Compare reactive, fixed-schedule, and predictive maintenance using the same event stream." />
      <div className="ledger-summary" aria-live="polite">
        <div><span className="eyebrow">Measured counterfactual</span><div className="ledger-headline-value"><MonoNumber>{reduction.toFixed(1)}%</MonoNumber><span>downtime reduction</span></div></div>
        <div><span className="eyebrow">Avoided cost</span><div className="ledger-headline-value"><MonoNumber>{formatInr(avoidedCost)}</MonoNumber><span>predictive vs reactive</span></div></div>
        <span className={`ledger-target${ledger.data.reduction_pct >= ledger.data.target_reduction_pct ? " ledger-target--passed" : ""}`}>{ledger.data.reduction_pct.toFixed(1)}% reduction / {ledger.data.target_reduction_pct.toFixed(0)}% target</span>
      </div>
      <div className="ledger-toolbar">
        <div><span className="eyebrow">Prediction threshold</span><MonoNumber>{effectiveTau.toFixed(2)}</MonoNumber></div>
        <label htmlFor="tau-slider">τ <input id="tau-slider" type="range" min="0" max="1" step="0.01" value={effectiveTau} onChange={(event) => setTau(Number(event.target.value))} aria-valuetext={`Prediction threshold ${effectiveTau.toFixed(2)}`} /></label>
        <span className="ledger-fetch-status" aria-live="polite">{ledger.isFetching ? "Recomputing policies..." : "Simulator ready"}</span>
      </div>
      {policies.length ? <div className="ledger-policy-grid ledger-policy-grid--chart" aria-label="Downtime by maintenance policy">
        <div className="ledger-target-line" style={{ left: `${targetPosition}%` }}><span>{ledger.data.target_reduction_pct.toFixed(0)}% target</span></div>
        {policies.map((policy, index) => {
          const width = `${Math.min(100, (policy.downtime_minutes / maximumDowntime) * 100)}%`;
          const color = index === 0 ? tokens.cat1 : index === 1 ? tokens.cat2 : tokens.cat3;
          return <motion.section className="panel ledger-policy-card" key={policy.policy} initial={reducedMotion ? false : { opacity: 0, y: 12 }} animate={{ opacity: 1, y: 0 }} transition={reducedMotion ? { duration: 0 } : { delay: index * 0.08, type: "spring", stiffness: 180, damping: 24 }}><div className="policy-heading"><span className="eyebrow">{policy.policy.replaceAll("_", " ")}</span><MonoNumber>{policy.downtime_minutes.toLocaleString("en-GB")} min</MonoNumber></div><div className="policy-bar"><motion.span initial={reducedMotion ? { width } : { width: 0 }} animate={{ width }} transition={reducedMotion ? { duration: 0 } : { type: "spring", stiffness: 180, damping: 24 }} style={{ background: color }} /></div><div className="policy-facts"><span>avoided cost</span><MonoNumber>{formatInr(policy.avoided_cost_inr)}</MonoNumber></div></motion.section>;
        })}
      </div> : <div className="honest-state"><Info size={20} aria-hidden="true" /><div><strong>No policy data returned</strong><p>The downtime simulator is reachable, but it returned no policy rows.</p></div></div>}
      <button className="quiet-button ledger-missed-toggle" type="button" aria-expanded={showMissed} onClick={() => setShowMissed((visible) => !visible)}><AlertTriangle size={15} aria-hidden="true" />{showMissed ? "Hide what the model missed" : `What the model missed (${missedFailures.length})`}</button>
      {showMissed && <motion.div className="ledger-missed-panel" initial={reducedMotion ? false : { opacity: 0, height: 0 }} animate={{ opacity: 1, height: "auto" }} transition={reducedMotion ? { duration: 0 } : undefined}><Panel title="What the model missed" className="ledger-missed-content">{missedFailures.length ? <div className="missed-failure-list">{missedFailures.map((failure) => <article className="missed-failure" key={failure.event_id}><div><strong>{failure.machine_id}</strong><span>{failure.fault_mode.replaceAll("_", " ")} · {failure.severity}</span><small>{formatTimestamp(failure.onset_ts)}</small></div><MonoNumber>{formatInr(failure.cost_inr)}</MonoNumber></article>)}</div> : <div className="honest-state honest-state--compact"><CheckCircle2 size={18} aria-hidden="true" /><div><strong>No uncaught failures at this threshold</strong><p>The predictive policy matched every labelled event in the current simulation.</p></div></div>}</Panel></motion.div>}
    </div>
  );
}

function TrustPanelSkeleton() {
  return <div className="trust-panel-skeleton" aria-hidden="true"><Skeleton width="78%" height="16px" /><Skeleton width="100%" height="54px" /><Skeleton width="62%" height="12px" /></div>;
}

function TrustSkeleton() {
  return <div className="screen"><ScreenHeader kicker="Model governance" title="Trust & Evals" description="Loading model quality, audit, and data-integrity status." /><div className="trust-overview-grid"><Panel title="KPI gates" className="trust-panel"><TrustPanelSkeleton /></Panel><Panel title="LLM resilience control" className="trust-panel"><TrustPanelSkeleton /></Panel></div><div className="trust-grid"><Panel title="Evaluation report" className="trust-panel"><TrustPanelSkeleton /></Panel><Panel title="Operational metrics" className="trust-panel"><TrustPanelSkeleton /></Panel><Panel title="System posture" className="trust-panel"><TrustPanelSkeleton /></Panel></div></div>;
}

function TrustTargetBar({ id, label, value, target }: { id: string; label: string; value: number | null; target: number }) {
  const visibleValue = value == null ? 0 : clamp(value, 0, 100);
  return (
    <div className="trust-target-kpi">
      <div className="trust-target-heading"><span>{label}</span><MonoNumber>{value == null ? "—" : `${value.toFixed(1)}%`}</MonoNumber></div>
      <div className="trust-target-track" data-testid={`${id}-target-track`} aria-label={`${label} ${value == null ? "unavailable" : `${value.toFixed(1)} percent`}, target ${target} percent`}>
        <span className="trust-target-fill" style={{ width: `${visibleValue}%` }} />
        <span className="trust-target-marker" data-testid={`${id}-target-line`} style={{ left: `${target}%` }}><span>{target}% target</span></span>
      </div>
      <div className="trust-target-scale"><span>0%</span><span>100%</span></div>
    </div>
  );
}

function TrustUnavailable({ title, detail }: { title: string; detail: string }) {
  return <div className="honest-state honest-state--compact"><CircleAlert size={17} aria-hidden="true" /><div><strong>{title}</strong><p>{detail}</p></div></div>;
}

export function TrustScreen() {
  const evaluation = useQuery({ queryKey: ["eval-report"], queryFn: () => fetchJson<EvalReportResponse>("/eval/report"), staleTime: 60_000 });
  const metrics = useQuery({ queryKey: ["trust-metrics"], queryFn: () => fetchJson<MetricsResponse>("/metrics"), staleTime: 15_000, refetchInterval: 15_000 });
  const health = useQuery({ queryKey: ["trust-health"], queryFn: () => fetchJson<HealthResponse>("/health"), staleTime: 15_000, refetchInterval: 15_000 });
  const ledger = useQuery({ queryKey: ["trust-ledger"], queryFn: () => fetchJson<LedgerResponse>("/ledger"), staleTime: 30_000 });
  const audit = useQuery({ queryKey: ["audit-verify"], queryFn: () => fetchJson<AuditVerifyResponse>("/audit/verify"), staleTime: 30_000 });
  const dlq = useQuery({ queryKey: ["ingest-dlq"], queryFn: () => fetchJson<DlqItem[]>("/ingest/dlq"), staleTime: 15_000 });
  const quarantine = useQuery({ queryKey: ["security-quarantine"], queryFn: () => fetchJson<QuarantineEntry[]>("/security/quarantine"), staleTime: 15_000 });
  const offline = useQuery({ queryKey: ["llm-offline"], queryFn: () => fetchJson<LlmOfflineResponse>("/llm/offline"), staleTime: 5_000 });
  const queryClient = useQueryClient();
  const release = useMutation({
    mutationFn: ({ quarantineId, reason }: { quarantineId: string; reason: string }) => fetchJson<QuarantineReleaseResponse>(`/security/quarantine/${encodeURIComponent(quarantineId)}/release`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ reason }) }),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["security-quarantine"] }),
  });
  const offlineToggle = useMutation({
    mutationFn: (enabled: boolean) => fetchJson<LlmOfflineResponse>("/llm/offline", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ enabled }) }),
    onSuccess: (state) => {
      queryClient.setQueryData(["llm-offline"], state);
      void queryClient.invalidateQueries({ queryKey: ["diagnosis"] });
    },
  });
  if (evaluation.isPending && metrics.isPending && health.isPending && ledger.isPending && audit.isPending && dlq.isPending && quarantine.isPending && offline.isPending) return <TrustSkeleton />;

  const accuracy = evaluation.data?.metrics.find((metric) => metric.name === "balanced_accuracy")?.value;
  const rejectionMetrics = Object.entries(metrics.data?.guardrail_rejections_by_rule ?? {});
  const evaluatedRejections = (evaluation.data?.summary?.hallucination?.scenarios ?? []).filter((scenario) => !scenario.guardrail_accepted).map((scenario) => ({ rule: scenario.kind === "empty_evidence" ? "insufficient_evidence" : "guardrail_contract", count: 1, source: scenario.id }));
  const rejectionEntries = rejectionMetrics.length ? rejectionMetrics.map(([rule, count]) => ({ rule, count, source: "persisted audit metrics" })) : evaluatedRejections;
  const injectionNotes: EvalInjectionNote[] = evaluation.data?.summary?.injection?.notes ?? [];
  const plantedFindings = injectionNotes.map((note) => ({ note, actual: (quarantine.data ?? []).find((item) => item.workorder_id === note.workorder_id) }));
  const llmHealth = health.data?.dependencies.find((dependency) => dependency.name === "llm");
  const latencyEntries: Array<[string, { samples: number; p50_ms: number; p95_ms: number }]> = Object.entries(metrics.data?.agent_latencies ?? {});
  const evaluationLatency = evaluation.data?.summary?.latency;
  const totalLatencyEntries: Array<[string, { samples: number; p50_ms: number; p95_ms: number }]> = latencyEntries.length ? latencyEntries : evaluationLatency ? [["evaluation total", { samples: 0, p50_ms: evaluationLatency.p50_ms, p95_ms: evaluationLatency.p95_ms }]] : [];

  return (
    <div className="screen trust-screen">
      <ScreenHeader kicker="Model governance" title="Trust & Evals" description="Quality, provenance, and data-integrity signals from the live API." />
      <div className="trust-overview-grid">
        <Panel title="KPI gates" className="trust-panel">
          <div className="trust-kpi-caption"><span>Measured values against the acceptance lines</span><span className="muted">live evaluation + simulator</span></div>
          <TrustTargetBar id="trust-accuracy" label="Balanced accuracy" value={accuracy == null ? null : accuracy * 100} target={85} />
          <TrustTargetBar id="trust-reduction" label="Downtime reduction" value={ledger.data?.reduction_pct ?? null} target={20} />
        </Panel>
        <Panel title="LLM resilience control" className="trust-panel">
          {offline.isPending ? <TrustPanelSkeleton /> : offline.isError || !offline.data ? <TrustUnavailable title="Offline control unavailable" detail="The presenter control could not be read from the API." /> : <div className="trust-control">
            <label className="trust-toggle"><input type="checkbox" checked={offline.data.enabled} onChange={(event) => offlineToggle.mutate(event.target.checked)} disabled={offlineToggle.isPending} /><span><b>Force deterministic templates</b><small>Flip the diagnosis path offline live.</small></span></label>
            <div className={`trust-mode trust-mode--${offline.data.effective_offline ? "offline" : "online"}`} role="status"><span className="trust-mode-dot" aria-hidden="true" /><span>{offline.data.effective_offline ? "Offline diagnosis mode active" : "Gateway diagnosis mode active"}</span><MonoNumber>{offline.data.mode}</MonoNumber></div>
            {offlineToggle.isError && <span className="trust-control-error">The mode change was rejected; the previous state is unchanged.</span>}
          </div>}
        </Panel>
      </div>
      <div className="trust-grid">
        <Panel title="Evaluation report" className="trust-panel trust-panel--wide">{evaluation.isPending ? <TrustPanelSkeleton /> : evaluation.isError || !evaluation.data ? <TrustUnavailable title="Evaluation report unavailable" detail="The persisted model manifest could not be read." /> : <><div className="trust-metadata"><span>model <MonoNumber>{evaluation.data.summary?.model ?? evaluation.data.model_version}</MonoNumber></span><span>prompt <MonoNumber>{evaluation.data.prompt_version ?? "unknown"}</MonoNumber></span><span>mode <MonoNumber>{evaluation.data.run_mode ?? evaluation.data.summary?.run_mode ?? "unknown"}</MonoNumber></span><span>{formatTimestamp(evaluation.data.generated_at)}</span></div>{evaluation.data.summary?.reliability && <div className="trust-metadata"><span>reliability <MonoNumber>{evaluation.data.summary.reliability.completed}/{evaluation.data.summary.reliability.runs}</MonoNumber></span><span>{evaluation.data.summary.reliability.unhandled} unhandled</span></div>}<div className="metric-list">{evaluation.data.metrics.length ? evaluation.data.metrics.map((metric) => <div className="metric-row" key={metric.name}><div><b>{metric.name.replaceAll("_", " ")}</b><span>{metric.sample_size.toLocaleString("en-GB")} samples · target {metric.target == null ? "not set" : `${(metric.target * 100).toFixed(0)}%`}</span></div><MonoNumber>{(metric.value * 100).toFixed(1)}%</MonoNumber><span className={`metric-status metric-status--${metric.passed ? "passed" : "failed"}`}>{metric.passed ? "passed" : "below target"}</span></div>) : <TrustUnavailable title="No evaluation metrics returned" detail="The API returned a valid report with no metric rows." />}</div></>}</Panel>
        <Panel title="Operational metrics" className="trust-panel">{metrics.isPending ? <TrustPanelSkeleton /> : metrics.isError || !metrics.data ? <TrustUnavailable title="Metrics unavailable" detail="Counters will return when the observability endpoint reconnects." /> : <><div className="trust-metadata"><span>requests <MonoNumber>{metrics.data.requests_total.toLocaleString("en-GB")}</MonoNumber></span><span>DLQ <MonoNumber>{metrics.data.dlq_depth}</MonoNumber></span></div><div className="trust-subheading">Guardrail rejection log by rule</div>{rejectionEntries.length ? <div className="metric-list">{rejectionEntries.map((entry) => <div className="metric-row" key={`${entry.rule}-${entry.source}`}><div><b>{entry.rule.replaceAll("_", " ")}</b><span>{entry.source}</span></div><MonoNumber>{entry.count}</MonoNumber><span className="metric-status metric-status--failed">rejected</span></div>)}</div> : <TrustUnavailable title="No guardrail rejections recorded" detail="The live audit metrics contain no rejection rows yet." />}<div className="trust-subheading trust-subheading--spaced">Latency and token totals</div>{totalLatencyEntries.length ? <div className="metric-list">{totalLatencyEntries.map(([agent, latency]) => <div className="metric-row" key={agent}><div><b>{agent.replaceAll("_", " ")}</b><span>p50 / p95 milliseconds</span></div><MonoNumber>{latency.p50_ms.toFixed(1)} / {latency.p95_ms.toFixed(1)}</MonoNumber><span className="metric-status">{latency.samples ? `${latency.samples} samples` : "eval report"}</span></div>)}</div> : <TrustUnavailable title="No latency samples" detail="No stage timings have been persisted yet." />}<div className="trust-token-row"><span>prompt tokens <MonoNumber>{metrics.data.llm_tokens.prompt.toLocaleString("en-GB")}</MonoNumber></span><span>completion tokens <MonoNumber>{metrics.data.llm_tokens.completion.toLocaleString("en-GB")}</MonoNumber></span><span>token cost <MonoNumber>{formatInr(metrics.data.llm_tokens.cost_inr, 2)} indicative</MonoNumber></span></div></>}</Panel>
        <Panel title="System posture" className="trust-panel">{health.isPending ? <TrustPanelSkeleton /> : health.isError || !health.data ? <TrustUnavailable title="Health posture unavailable" detail="Dependency state will return on the next health poll." /> : <><div className="trust-metadata"><span>overall <MonoNumber>{health.data.status}</MonoNumber></span><span>{formatTimestamp(health.data.generated_at)}</span></div><div className="dependency-list">{health.data.dependencies.map((dependency) => <div className="trust-dependency" key={dependency.name}><span className={`dependency-dot dependency-dot--${dependency.status}`} aria-hidden="true" /><div><b>{dependency.name}</b><span>{dependency.status} · {dependency.breaker_state}</span></div>{dependency.name === "llm" && <span className={`tls-posture ${dependency.tls_verified ? "" : "tls-posture--warning"}`}>TLS {dependency.tls_verified ? "verified" : "not verified"}</span>}</div>)}</div><div className="trust-version-stamps"><span>model <MonoNumber>{llmHealth?.model_name ?? "unknown"}</MonoNumber></span><span>provider <MonoNumber>{llmHealth?.provider ?? "unknown"}</MonoNumber></span></div></>}</Panel>
        <Panel title="Audit chain" className="trust-panel">{audit.isPending ? <TrustPanelSkeleton /> : audit.isError || !audit.data ? <TrustUnavailable title="Audit status unavailable" detail="Verification will retry when the ledger endpoint reconnects." /> : <div className={`trust-verdict trust-verdict--${audit.data.intact ? "passed" : "failed"}`}><CheckCircle2 size={25} aria-hidden="true" /><strong>{audit.data.intact ? "Chain intact" : "Tamper detected"}</strong><MonoNumber>{audit.data.checked_entries} entries checked</MonoNumber>{audit.data.first_invalid_seq && <span>First invalid sequence: {audit.data.first_invalid_seq}</span>}</div>}</Panel>
        <Panel title="DLQ contents" className="trust-panel">{dlq.isPending ? <TrustPanelSkeleton /> : dlq.isError || !dlq.data ? <TrustUnavailable title="DLQ status unavailable" detail="Data-integrity rows will return when ingest reconnects." /> : dlq.data.length ? <div className="trust-log-list">{dlq.data.map((item) => <article className="trust-log-entry" key={item.id}><b>{item.machine_id}</b><span>{item.reason}</span><small>{formatTimestamp(item.ts)}</small></article>)}</div> : <div className="trust-verdict trust-verdict--neutral"><FileJson size={25} aria-hidden="true" /><strong>DLQ is empty</strong><span>No ingest rows are awaiting review.</span></div>}</Panel>
        <Panel title="Injection quarantine log" className="trust-panel trust-panel--wide">{quarantine.isPending || evaluation.isPending ? <TrustPanelSkeleton /> : quarantine.isError || evaluation.isError || !evaluation.data ? <TrustUnavailable title="Quarantine log unavailable" detail="Injection findings remain blocked while the Trust endpoints reconnect." /> : injectionNotes.length ? <><div className="trust-metadata"><span>{evaluation.data.summary?.injection?.total ?? injectionNotes.length} planted notes</span><span>{evaluation.data.summary?.injection?.matched ?? quarantine.data?.length ?? 0} scanner matches</span><span>{evaluation.data.summary?.injection?.known_evasions ?? 0} deliberate evasions</span></div><div className="trust-log-list">{plantedFindings.map(({ note, actual }) => <article className="trust-log-entry trust-log-entry--injection" key={note.workorder_id}><div><b>{note.workorder_id}</b><span>{note.matched ? note.matched_patterns.join(", ") || note.pattern : "deliberate scanner evasion · layered defence"}</span>{actual?.text && <small>{actual.text}</small>}</div>{actual?.status === "quarantined" ? <button className="quiet-button" type="button" onClick={() => release.mutate({ quarantineId: actual.quarantine_id, reason: "Reviewed by an authorised maintenance user" })} disabled={release.isPending}>{release.isPending ? "Recording review…" : "Mark reviewed"}</button> : <span className={`metric-status metric-status--${note.matched ? "passed" : "failed"}`}>{note.matched ? "quarantined" : "not detected"}</span>}</article>)}</div></> : quarantine.data?.length ? <div className="trust-log-list">{quarantine.data.map((item) => <article className="trust-log-entry" key={item.quarantine_id}><b>{item.workorder_id}</b><span>{item.patterns.join(", ")}</span><small>{item.text}</small></article>)}</div> : <div className="trust-verdict trust-verdict--neutral"><CheckCircle2 size={25} aria-hidden="true" /><strong>No injection findings</strong><span>Retrieved ERP notes are scanned before they can enter a model prompt.</span></div>}</Panel>
      </div>
    </div>
  );
}
