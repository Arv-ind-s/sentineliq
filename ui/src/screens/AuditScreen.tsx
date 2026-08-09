import { useState } from "react";
import { ChevronDown, CircleAlert, ShieldCheck, ShieldX } from "lucide-react";
import { useQuery } from "@tanstack/react-query";
import type { AuditEntry, AuditVerifyResponse } from "../types/api";
import { ApiError, fetchJson } from "../lib/api";
import { MonoNumber, Panel, Skeleton } from "../components/primitives";

function formatTimestamp(timestamp: string) {
  const date = new Date(timestamp);
  if (Number.isNaN(date.getTime())) return "Timestamp unavailable";
  return new Intl.DateTimeFormat(undefined, {
    dateStyle: "medium",
    timeStyle: "medium",
    timeZone: "UTC",
  }).format(date);
}

function isForbidden(error: unknown) {
  return error instanceof ApiError && error.status === 403;
}

function AuditHeader() {
  return <header className="screen-header"><div className="eyebrow">Governance / evidence chain</div><h1>Audit ledger</h1><p>Review consequential decisions and verify that the append-only evidence chain has not been altered.</p></header>;
}

function AuditLoading() {
  return <div className="audit-loading" aria-label="Loading audit ledger"><Skeleton width="100%" height="52px" /><Skeleton width="100%" height="260px" /></div>;
}

function VerificationBanner({ verification }: { verification: AuditVerifyResponse }) {
  const intact = verification.intact;
  const firstInvalid = verification.first_invalid_seq;
  return (
    <div className={`audit-verify-banner audit-verify-banner--${intact ? "intact" : "broken"}`} role={intact ? "status" : "alert"}>
      {intact ? <ShieldCheck size={19} aria-hidden="true" /> : <ShieldX size={19} aria-hidden="true" />}
      <div>
        <strong>{intact ? `Chain intact (${verification.entries} entries)` : `Chain broken at seq ${firstInvalid ?? "unknown"}`}</strong>
        <span>{intact ? `${verification.checked_entries} entries recomputed from the genesis hash.` : `${verification.checked_entries} entries verified before the first invalid link.`}</span>
      </div>
    </div>
  );
}

function AuditTable({ entries, firstInvalidSeq }: { entries: AuditEntry[]; firstInvalidSeq: number | null }) {
  const [expandedSeq, setExpandedSeq] = useState<number | null>(null);
  const newestFirst = [...entries].sort((left, right) => right.seq - left.seq);

  return (
    <div className="audit-table-wrap">
      <table className="audit-table">
        <caption>Audit ledger entries, newest first</caption>
        <thead>
          <tr><th scope="col">Seq</th><th scope="col">Timestamp (UTC)</th><th scope="col">Actor</th><th scope="col">Action</th><th scope="col">Subject</th><th scope="col"><span className="sr-only">Details</span></th></tr>
        </thead>
        <tbody>
          {newestFirst.map((entry) => {
            const expanded = expandedSeq === entry.seq;
            const tampered = firstInvalidSeq === entry.seq;
            return (
              <tr className={tampered ? "audit-row audit-row--tampered" : "audit-row"} key={entry.seq}>
                <td><MonoNumber>{entry.seq}</MonoNumber></td>
                <td className="audit-timestamp">{formatTimestamp(entry.ts)}</td>
                <td>{entry.actor}</td>
                <td><span className="audit-action">{entry.action}</span></td>
                <td className="audit-subject">{entry.subject}</td>
                <td className="audit-details-cell">
                  <button className="audit-expand-button" type="button" aria-expanded={expanded} aria-controls={`audit-details-${entry.seq}`} onClick={() => setExpandedSeq(expanded ? null : entry.seq)}>
                    <ChevronDown size={15} aria-hidden="true" />
                    <span>{expanded ? "Hide" : "Expand"}</span>
                  </button>
                  {expanded && <div className="audit-entry-details" id={`audit-details-${entry.seq}`}>
                    <div><span>PREV_HASH</span><code>{entry.prev_hash}</code></div>
                    <div><span>HASH</span><code>{entry.hash}</code></div>
                    <div><span>PAYLOAD</span><pre>{JSON.stringify(entry.payload, null, 2)}</pre></div>
                  </div>}
                </td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}

export function AuditScreen() {
  const entries = useQuery({ queryKey: ["audit-ledger"], queryFn: () => fetchJson<AuditEntry[]>("/audit"), staleTime: 15_000 });
  const verification = useQuery({ queryKey: ["audit-verify"], queryFn: () => fetchJson<AuditVerifyResponse>("/audit/verify"), staleTime: 15_000 });

  if (isForbidden(entries.error) || isForbidden(verification.error)) {
    return <div className="screen"><AuditHeader /><div className="honest-state audit-access-denied" role="alert"><ShieldX size={20} aria-hidden="true" /><div><strong>Audit access restricted</strong><p>This ledger is available to planners and administrators. The API denied the current role.</p></div></div></div>;
  }

  return (
    <div className="screen audit-screen">
      <AuditHeader />
      {verification.isPending ? <div className="audit-verify-loading"><Skeleton width="100%" height="68px" /></div> : verification.data ? <VerificationBanner verification={verification.data} /> : <div className="honest-state audit-verify-error" role="alert"><CircleAlert size={19} aria-hidden="true" /><div><strong>Chain verification unavailable</strong><p>The ledger entries may still be readable, but integrity has not been inferred while the verification endpoint is unavailable.</p></div></div>}
      {entries.isPending ? <AuditLoading /> : entries.isError ? <div className="honest-state" role="alert"><CircleAlert size={20} aria-hidden="true" /><div><strong>Audit ledger unavailable</strong><p>The audit service did not return entries. No ledger state is inferred.</p></div></div> : entries.data?.length ? <Panel title={`Ledger entries · ${entries.data.length} loaded`} className="audit-panel"><AuditTable entries={entries.data} firstInvalidSeq={verification.data?.first_invalid_seq ?? null} /></Panel> : <Panel title="Ledger entries" className="audit-panel"><div className="audit-empty"><ShieldCheck size={20} aria-hidden="true" /><div><strong>No audit entries yet</strong><p>The chain is ready and will show consequential events when they occur.</p></div></div></Panel>}
    </div>
  );
}
