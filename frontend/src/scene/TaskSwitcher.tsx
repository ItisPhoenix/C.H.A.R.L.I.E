import type { ReactElement } from "react";
import { useCharlieStore } from "../store/charlie";
import { useWorkspaceStore } from "../layout/workspaceStore";
import { sendCommand } from "../runtime/bridge";
import { isTaskWorkspaceEligible } from "./taskWorkspaceEligibility";

interface TaskSwitcherProps {
  forceShow?: boolean;
}

const ACTIVE_TASK_STATUSES = new Set([
  "queued", "planning", "waiting", "running", "paused", "approval_required", "verifying",
]);

export function TaskSwitcher({ forceShow }: TaskSwitcherProps): ReactElement | null {
  const tasks = useCharlieStore((s) => s.tasks);
  const activeWorkspace = useWorkspaceStore((s) => s.getActiveWorkspace());

  const taskList = Object.values(tasks).filter((task) => isTaskWorkspaceEligible(task, ACTIVE_TASK_STATUSES));

  // Contextual visibility rule: Only show when multiple tasks exist (> 1) or explicitly requested
  if (!forceShow && taskList.length <= 1) {
    return null;
  }

  return (
    <div
      className="absolute top-3 left-1/2 transform -translate-x-1/2 z-40 flex items-center gap-2 px-3 py-1 rounded-full bg-slate-950/80 border border-cyan-500/20 shadow-2xl backdrop-blur-md font-mono select-none pointer-events-auto"
      role="toolbar"
      aria-label="Active Tasks HUD Rail"
    >
      <span className="px-1 text-[9px] text-cyan-400/80 font-bold tracking-wider uppercase border-r border-cyan-500/20 pr-2">
        TASKS [{taskList.length}]
      </span>

      <div className="flex items-center gap-1.5">
        {taskList.map((task) => {
          const isCurrent = activeWorkspace?.taskId === task.id;
          const progressPct =
            task.progress !== undefined && task.progress !== null
              ? Math.round(task.progress * 100)
              : task.totalSteps > 0
                ? Math.round((task.currentStep / task.totalSteps) * 100)
                : null;

          return (
            <button
              key={task.id}
              type="button"
              onClick={() => {
                if (task.id) sendCommand("presentation_command", { action: "focus_task", task_id: task.id });
              }}
              className={`flex items-center gap-2 px-3 py-1 rounded-full text-xs transition cursor-pointer ${
                isCurrent
                  ? "bg-cyan-950/80 border border-cyan-400/60 text-cyan-200 shadow-sm shadow-cyan-500/30 font-semibold"
                  : "text-slate-400 hover:text-cyan-200 hover:bg-slate-900/60 border border-transparent"
              }`}
              title={`Switch to task: ${task.title}`}
            >
              <span
                className={`w-1.5 h-1.5 rounded-full ${
                  task.status === "running"
                    ? "bg-emerald-400 animate-pulse shadow-sm shadow-emerald-400"
                    : task.status === "failed"
                      ? "bg-red-400"
                      : task.status === "approval_required"
                        ? "bg-amber-400 animate-bounce"
                        : "bg-cyan-400"
                }`}
              />
              <span className="truncate max-w-[160px] font-sans text-[11px]">
                {task.title}
              </span>
              {progressPct !== null && (
                <span className="text-[10px] text-cyan-400 font-mono">
                  {progressPct}%
                </span>
              )}
            </button>
          );
        })}
      </div>
    </div>
  );
}
