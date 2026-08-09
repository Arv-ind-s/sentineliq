import type { UserRole } from "../types/api";

export function roleLabel(role: UserRole) {
  return role.replaceAll("_", " ");
}

export type RoleProfile = {
  role: UserRole;
  label: string;
  workspace: string;
  mission: string;
  shortMission: string;
  defaultRoute: string;
  capabilities: readonly string[];
};

/** Keep the role story consistent at sign-in and throughout the workspace. */
export const ROLE_PROFILES: Record<UserRole, RoleProfile> = {
  operator: {
    role: "operator", label: "Floor operator", workspace: "Live monitoring workspace",
    mission: "Monitor the plant, spot abnormal conditions, and escalate emerging risk.",
    shortMission: "Monitor and escalate", defaultRoute: "/floor",
    capabilities: ["See live machine health", "Triage open alerts", "Escalate issues to maintenance"],
  },
  technician: {
    role: "technician", label: "Maintenance technician", workspace: "Maintenance response workspace",
    mission: "Investigate evidence, acknowledge maintenance work, and prepare safe execution.",
    shortMission: "Diagnose and respond", defaultRoute: "/schedule",
    capabilities: ["Inspect diagnosis evidence", "Review maintenance windows", "Acknowledge technician approvals"],
  },
  planner: {
    role: "planner", label: "Maintenance planner", workspace: "Planning and approval workspace",
    mission: "Turn evidence into an accountable maintenance plan and release approved work.",
    shortMission: "Plan and approve", defaultRoute: "/schedule",
    capabilities: ["Approve or reject recommendations", "Schedule maintenance windows", "Create governed ERP work orders"],
  },
  admin: {
    role: "admin", label: "System administrator", workspace: "System governance workspace",
    mission: "Oversee plant decisions, model trust, integrations, and the audit trail.",
    shortMission: "Govern and oversee", defaultRoute: "/trust",
    capabilities: ["Access every operational workspace", "Inspect trust and evaluation controls", "Review the immutable audit ledger"],
  },
};

export function roleProfile(role: UserRole): RoleProfile {
  return ROLE_PROFILES[role];
}
