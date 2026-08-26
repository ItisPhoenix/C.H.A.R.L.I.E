import type { RuntimeTask } from "../store/charlie";

/**
 * Full task workspaces require execution substance, not merely a live status.
 * This mirrors the runtime admission contract for the fields projected by the
 * canonical Task Journal.
 */
export function hasTaskWorkspaceSubstance(task: RuntimeTask): boolean {
  if (task.totalSteps > 0) return true;
  if (Boolean(task.currentAction?.trim())) return true;
  if (Boolean(task.waitingReason?.trim())) return true;
  if (Boolean(task.approvalReference?.trim())) return true;
  return (task.capabilityRequirements?.length ?? 0) > 0;
}

export function isTaskWorkspaceEligible(task: RuntimeTask, activeStatuses: ReadonlySet<string>): boolean {
  return activeStatuses.has(task.status) && hasTaskWorkspaceSubstance(task);
}
