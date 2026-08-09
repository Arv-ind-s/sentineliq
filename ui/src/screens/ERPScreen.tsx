import { useEffect, useMemo, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { AlertTriangle, CheckCircle2, Database, PackageSearch, RefreshCw, Search, Wrench } from "lucide-react";
import { MonoNumber, Panel, Skeleton } from "../components/primitives";
import { fetchJson } from "../lib/api";
import { useAuth } from "../lib/auth-context";
import type { Approval, ERPActionResponse, ERPApprovalExecute, ERPPartList, ERPStatus, ERPWorkOrderList } from "../types/api";

const inr = new Intl.NumberFormat("en-IN", { style: "currency", currency: "INR", maximumFractionDigits: 0 });

function when(value: string) {
  return new Intl.DateTimeFormat("en-IN", { dateStyle: "medium", timeStyle: "short", timeZone: "Asia/Kolkata" }).format(new Date(value));
}

export function ERPScreen() {
  const { user } = useAuth();
  const queryClient = useQueryClient();
  const [asset, setAsset] = useState("");
  const [statusFilter, setStatusFilter] = useState("");
  const [search, setSearch] = useState("");
  const [selectedApproval, setSelectedApproval] = useState("");
  const [notice, setNotice] = useState<string | null>(null);

  const erpStatus = useQuery({ queryKey: ["erp", "status"], queryFn: () => fetchJson<ERPStatus>("/erp/status"), refetchInterval: 10_000 });
  const history = useQuery({ queryKey: ["erp", "workorders"], queryFn: () => fetchJson<ERPWorkOrderList>("/erp/workorders?limit=500"), staleTime: 5_000 });
  const inventory = useQuery({ queryKey: ["erp", "parts"], queryFn: () => fetchJson<ERPPartList>("/erp/parts"), staleTime: 15_000 });
  const approvals = useQuery({ queryKey: ["approvals", "approved"], queryFn: () => fetchJson<Approval[]>("/approvals?state=approved"), staleTime: 5_000 });

  const usableApprovals = useMemo(() => {
    return (approvals.data ?? []).filter((approval) => approval.autonomy_level === "A0" && approval.action_payload !== null);
  }, [approvals.data]);

  useEffect(() => {
    if (!selectedApproval && usableApprovals[0]) setSelectedApproval(usableApprovals[0].id);
  }, [selectedApproval, usableApprovals]);

  const selected = usableApprovals.find((approval) => approval.id === selectedApproval);
  const assets = useMemo(() => [...new Set((history.data?.items ?? []).map((item) => item.asset_id))].sort(), [history.data]);
  const visibleOrders = useMemo(() => {
    const needle = search.trim().toLocaleLowerCase();
    return (history.data?.items ?? []).filter((item) => {
      if (asset && item.asset_id !== asset) return false;
      if (statusFilter && item.status !== statusFilter) return false;
      return !needle || [item.workorder_id, item.asset_id, item.fault_code, item.description, item.technician_notes, ...item.parts].join(" ").toLocaleLowerCase().includes(needle);
    });
  }, [asset, history.data, search, statusFilter]);

  const createWorkOrder = useMutation({
    mutationFn: (payload: ERPApprovalExecute) => fetchJson<ERPActionResponse>("/erp/workorders", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(payload) }),
    onSuccess: (result) => {
      setNotice(result.message);
      void queryClient.invalidateQueries({ queryKey: ["erp"] });
      void queryClient.invalidateQueries({ queryKey: ["approvals"] });
    },
    onError: (error) => setNotice(error instanceof Error ? error.message : "The approved work order could not be submitted."),
  });

  const submit = () => {
    if (!selected) return;
    createWorkOrder.mutate({ approval_id: selected.id });
  };

  const canWrite = user?.role === "planner" || user?.role === "admin";
  const degraded = erpStatus.data?.source === "cache" || history.data?.degraded || inventory.data?.degraded;

  return (
    <div className="erp-screen">
      <header className="screen-header">
        <span className="eyebrow">Maintenance operations</span>
        <h1>ERP Work Orders</h1>
        <p>Browse the complete maintenance history, inspect parts availability, and create only approval-backed work orders through the governed ERP agent.</p>
      </header>

      {erpStatus.isPending ? <Skeleton width="100%" height="64px" /> : erpStatus.isError ? <div className="erp-service-banner erp-service-banner--degraded"><AlertTriangle size={18} /><div><strong>ERP status unavailable</strong><span>The application could not determine whether live ERP or cached history is active.</span></div></div> : <div className={`erp-service-banner${degraded ? " erp-service-banner--degraded" : ""}`}>{degraded ? <AlertTriangle size={18} /> : <CheckCircle2 size={18} />}<div><strong>{degraded ? "Degraded · cached history active" : "ERP connected · live data"}</strong><span>{erpStatus.data?.detail}</span></div><button className="quiet-button" type="button" onClick={() => { void queryClient.invalidateQueries({ queryKey: ["erp"] }); }}><RefreshCw size={13} />Refresh</button></div>}

      <section className="erp-kpis" aria-label="ERP summary">
        <Panel title="Historical work orders"><div className="erp-kpi"><Database size={17} /><MonoNumber>{erpStatus.data?.historical_workorders ?? "—"}</MonoNumber><span>{erpStatus.data?.source ?? "checking"} source</span></div></Panel>
        <Panel title="Pending synchronization"><div className="erp-kpi"><RefreshCw size={17} /><MonoNumber>{erpStatus.data?.pending_sync ?? "—"}</MonoNumber><span>{erpStatus.data?.failed_sync ?? 0} failed</span></div></Panel>
        <Panel title="Parts catalog"><div className="erp-kpi"><PackageSearch size={17} /><MonoNumber>{inventory.data?.items.length ?? "—"}</MonoNumber><span>{inventory.data?.items.filter((part) => part.in_stock).length ?? 0} stocked</span></div></Panel>
      </section>

      <div className="erp-layout">
        <div className="erp-primary">
          <Panel title="Historical work orders" className="erp-history-panel">
            <div className="erp-filters">
              <label><span>Asset</span><select value={asset} onChange={(event) => setAsset(event.target.value)}><option value="">All assets</option>{assets.map((item) => <option key={item}>{item}</option>)}</select></label>
              <label><span>Status</span><select value={statusFilter} onChange={(event) => setStatusFilter(event.target.value)}><option value="">All statuses</option><option value="open">Open</option><option value="in_progress">In progress</option><option value="completed">Completed</option><option value="cancelled">Cancelled</option></select></label>
              <label className="erp-search"><span>Search</span><span><Search size={14} /><input value={search} onChange={(event) => setSearch(event.target.value)} placeholder="ID, fault, note, part…" /></span></label>
              <MonoNumber>{visibleOrders.length} shown</MonoNumber>
            </div>
            {history.isPending ? <div className="erp-loading"><Skeleton width="100%" height="42px" /><Skeleton width="100%" height="42px" /><Skeleton width="100%" height="42px" /></div> : history.isError ? <div className="honest-state"><AlertTriangle size={18} /><div><strong>Work-order history unavailable</strong><p>Neither the ERP service nor its application mirror could be read.</p></div></div> : <div className="erp-table-wrap"><table className="erp-table"><caption>ERP work-order history</caption><thead><tr><th>Work order</th><th>Asset / fault</th><th>Description</th><th>Status</th><th>Opened</th><th>Downtime</th><th>Cost</th><th>Parts</th></tr></thead><tbody>{visibleOrders.map((order) => <tr key={order.workorder_id}><td><MonoNumber>{order.workorder_id}</MonoNumber><small>{order.order_type}</small></td><td><b>{order.asset_id}</b><small>{order.fault_code}</small></td><td><span title={order.technician_notes}>{order.description}</span><small>{order.labour_hours.toFixed(1)} labour h</small></td><td><span className={`erp-order-status erp-order-status--${order.status}`}>{order.status.replace("_", " ")}</span><small>{order.priority}</small></td><td><span>{when(order.created_at)}</span>{order.completed_at && <small>Closed {when(order.completed_at)}</small>}</td><td><MonoNumber>{order.downtime_minutes} min</MonoNumber><small>{order.lead_time_days}d lead</small></td><td><MonoNumber>{inr.format(order.cost_inr)}</MonoNumber></td><td><span>{order.parts.join(", ") || "None recorded"}</span></td></tr>)}</tbody></table>{visibleOrders.length === 0 && <div className="erp-empty">No work orders match these filters.</div>}</div>}
          </Panel>

          <Panel title="Parts & lead times">
            {inventory.isPending ? <Skeleton width="100%" height="110px" /> : inventory.isError ? <div className="honest-state honest-state--compact"><AlertTriangle size={16} /><div><strong>Parts unavailable</strong><p>The inventory catalog could not be loaded.</p></div></div> : <div className="erp-parts-grid">{inventory.data?.items.map((part) => <article key={part.part_id} className={part.in_stock ? "erp-part" : "erp-part erp-part--backorder"}><div><MonoNumber>{part.part_id}</MonoNumber><span className={part.in_stock ? "erp-stock" : "erp-stock erp-stock--out"}>{part.in_stock ? `${part.stock_quantity} in stock` : "Backorder"}</span></div><strong>{part.name}</strong><span>{part.fault_code} · {part.lead_time_days} day lead</span><MonoNumber>{inr.format(part.unit_cost_inr)}</MonoNumber></article>)}</div>}
          </Panel>
        </div>

        <aside className="erp-side">
          <Panel title="Retry approved ERP execution">
            {!canWrite ? <div className="honest-state honest-state--compact"><Wrench size={16} /><div><strong>Planner access required</strong><p>You can inspect ERP history, but only planners and administrators can submit an approved recommendation.</p></div></div> : usableApprovals.length === 0 ? <div className="honest-state honest-state--compact"><CheckCircle2 size={16} /><div><strong>No usable approvals</strong><p>Approve a recommendation in Maintenance first. ERP creation cannot bypass that decision.</p></div></div> : <form className="erp-create-form" onSubmit={(event) => { event.preventDefault(); submit(); }}>
              <label><span>Approved recommendation</span><select value={selectedApproval} onChange={(event) => setSelectedApproval(event.target.value)}>{usableApprovals.map((approval) => <option value={approval.id} key={approval.id}>{approval.machine_id} · {approval.prediction_id} · {approval.erp_sync_status}</option>)}</select></label>
              <div className="erp-form-note">The server will execute the immutable payload reviewed under <b>{selected?.id}</b>. The approval ID and payload hash make retries idempotent.</div>
              <pre className="approval-payload">{JSON.stringify(selected?.action_payload, null, 2)}</pre>
              <button className="primary-button" type="button" onClick={submit} disabled={createWorkOrder.isPending}>{createWorkOrder.isPending ? "Submitting…" : "Retry stored work order"}</button>
              {notice && <div className="erp-form-result" role="status">{notice}</div>}
            </form>}
          </Panel>

          <Panel title="Pending ERP sync">
            {erpStatus.data?.queue.length ? <div className="erp-sync-list">{erpStatus.data.queue.map((item) => <article key={item.prediction_id}><div><MonoNumber>{item.prediction_id}</MonoNumber><span className={`erp-order-status erp-order-status--${item.status === "failed" ? "cancelled" : "open"}`}>{item.status}</span></div><span>{item.attempts} attempts · {when(item.updated_at)}</span>{item.last_error && <small>{item.last_error}</small>}</article>)}</div> : <div className="erp-empty erp-empty--compact">No work orders are waiting to synchronize.</div>}
          </Panel>
        </aside>
      </div>
    </div>
  );
}
