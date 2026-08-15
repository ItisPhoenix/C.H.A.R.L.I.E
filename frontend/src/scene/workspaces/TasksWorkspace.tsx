import type { ReactElement } from "react";
import type { WorkspaceInstance } from "../../layout/workspaceStore";
import { useCharlieStore, type RuntimeTask } from "../../store/charlie";

export function TasksWorkspace({ workspace }: { workspace: WorkspaceInstance }): ReactElement {
  const content = workspace.contentState || {};
  const tasks = useCharlieStore((s) => s.tasks);
  const taskList = Object.values(tasks);

  // Identify current focused task or fallback to workspace payload
  const currentTask: RuntimeTask = (workspace.taskId && tasks[workspace.taskId])
    ? tasks[workspace.taskId]
    : taskList.find((t) => t.status === "running") || taskList[0] || {
        id: "task_01",
        title: String(content.title || workspace.title || "Autonomous Task Execution").replace(/^WORKSPACE\s*\/\/\s*/i, ""),
        status: "running",
        currentStep: 3,
        totalSteps: 5,
        progress: 0.6,
      };

  const steps = [
    { num: 1, title: "Initialize environment & load telemetry parameters", capability: "SystemCapability", status: "completed" },
    { num: 2, title: "Query regional maritime radar and transponder logs", capability: "ResearchCapability", status: "completed" },
    { num: 3, title: "Process spatial cluster anomaly vectors", capability: "ResearchCapability", status: "running" },
    { num: 4, title: "Validate cross-correlations with open source advisories", capability: "BrowserCapability", status: "pending" },
    { num: 5, title: "Render synthesized spatial intelligence workspace", capability: "PresentationResolver", status: "pending" },
  ];

  const statusColor = (st: string) => {
    switch (st) {
      case "running":
        return "text-emerald-400 bg-emerald-950/60 border-emerald-500/40";
      case "approval_required":
        return "text-amber-300 bg-amber-950/60 border-amber-500/40 animate-pulse";
      case "completed":
        return "text-cyan-300 bg-cyan-950/60 border-cyan-500/40";
      case "failed":
        return "text-red-400 bg-red-950/60 border-red-500/40";
      default:
        return "text-slate-400 bg-slate-900/60 border-slate-700/40";
    }
  };

  return (
    <div className="w-full h-full flex flex-col justify-between font-mono select-none text-left p-2 overflow-y-auto space-y-6">
      {/* 1. Header */}
      <div className="flex items-start justify-between border-b border-cyan-500/20 pb-4">
        <div>
          <div className="text-[10px] text-cyan-400 font-bold tracking-widest uppercase mb-1 flex items-center gap-2">
            <span className="w-1.5 h-1.5 rounded-full bg-cyan-400 animate-pulse" />
            TASK EXECUTION WORKSPACE
          </div>
          <h1 className="text-xl sm:text-2xl font-bold text-slate-100 uppercase tracking-tight font-sans">
            {currentTask.title}
          </h1>
          <div className="text-xs text-slate-400 tracking-wider mt-1">
            TASK ID: <span className="text-cyan-300">{currentTask.id}</span>
          </div>
        </div>

        {/* Task Status Badge */}
        <div className={`px-3 py-1.5 rounded-full border text-xs font-bold uppercase tracking-wider ${statusColor(currentTask.status)}`}>
          {currentTask.status}
        </div>
      </div>

      {/* 2. Main Grid: Current Task Execution Details (Left/Center) & Background Tasks Queue (Right) */}
      <div className="grid grid-cols-1 lg:grid-cols-12 gap-6 items-start">
        {/* Execution Journal & Steps */}
        <div className="lg:col-span-8 flex flex-col gap-5">
          {/* Progress Bar Header */}
          <div className="p-4 rounded-xl border border-cyan-500/20 bg-slate-950/60 backdrop-blur-md space-y-3">
            <div className="flex justify-between items-center text-xs">
              <span className="text-cyan-400 font-bold uppercase">
                Progress: Step {currentTask.currentStep} of {currentTask.totalSteps || 5}
              </span>
              <span className="text-slate-200 font-bold">
                {Math.round((currentTask.progress ?? 0.6) * 100)}%
              </span>
            </div>

            {/* Glowing progress track */}
            <div className="w-full h-2 rounded-full bg-slate-900 border border-cyan-500/30 overflow-hidden">
              <div
                className="h-full bg-gradient-to-r from-cyan-600 via-cyan-400 to-sky-300 transition-all duration-500 shadow-lg shadow-cyan-400/50"
                style={{ width: `${Math.round((currentTask.progress ?? 0.6) * 100)}%` }}
              />
            </div>
          </div>

          {/* Step-by-Step Task Journal */}
          <div className="p-4 rounded-xl border border-cyan-500/20 bg-slate-950/60 backdrop-blur-md space-y-3">
            <div className="text-xs font-semibold text-cyan-200 uppercase tracking-wider">
              EXECUTION PLAN & STATUS
            </div>

            <div className="space-y-2.5">
              {steps.map((step) => {
                const isRunning = step.status === "running";
                const isCompleted = step.status === "completed";

                return (
                  <div
                    key={step.num}
                    className={`p-3 rounded-lg border flex items-center justify-between gap-4 transition ${
                      isRunning
                        ? "bg-cyan-950/50 border-cyan-400/50 text-slate-100"
                        : isCompleted
                          ? "bg-slate-950/40 border-cyan-500/20 text-slate-300"
                          : "bg-slate-950/20 border-slate-800 text-slate-500"
                    }`}
                  >
                    <div className="flex items-center gap-3">
                      <div
                        className={`w-6 h-6 rounded-full flex items-center justify-center text-xs font-bold ${
                          isCompleted
                            ? "bg-cyan-900/80 text-cyan-300"
                            : isRunning
                              ? "bg-cyan-400 text-slate-950 animate-pulse"
                              : "bg-slate-800 text-slate-400"
                        }`}
                      >
                        {isCompleted ? "✓" : step.num}
                      </div>

                      <div className="text-left">
                        <div className="text-xs font-medium font-sans">
                          {step.title}
                        </div>
                        <div className="text-[10px] text-cyan-400/70 font-mono mt-0.5">
                          OWNER: {step.capability}
                        </div>
                      </div>
                    </div>

                    <div className="text-[10px] font-bold uppercase tracking-wider whitespace-nowrap">
                      {isRunning ? (
                        <span className="text-emerald-400 animate-pulse">RUNNING...</span>
                      ) : isCompleted ? (
                        <span className="text-cyan-400">DONE</span>
                      ) : (
                        <span className="text-slate-600">QUEUED</span>
                      )}
                    </div>
                  </div>
                );
              })}
            </div>
          </div>
        </div>

        {/* Secondary / Concurrent Tasks List */}
        <div className="lg:col-span-4 flex flex-col gap-4">
          <div className="text-left">
            <div className="text-xs font-semibold text-cyan-200 tracking-wider uppercase">
              CONCURRENT TASKS
            </div>
            <div className="text-[10px] text-cyan-400/60 uppercase">
              BACKGROUND QUEUE [{taskList.length}]
            </div>
          </div>

          <div className="space-y-2.5">
            {taskList.length === 0 ? (
              <div className="p-4 rounded-xl border border-cyan-500/15 bg-slate-950/40 text-xs text-slate-500 italic">
                No background tasks in queue.
              </div>
            ) : (
              taskList.map((t) => (
                <div
                  key={t.id}
                  className={`p-3 rounded-xl border transition flex flex-col gap-1.5 text-left ${
                    t.id === currentTask.id
                      ? "bg-cyan-950/40 border-cyan-400/50"
                      : "bg-slate-950/60 border-cyan-500/15 hover:border-cyan-500/30"
                  }`}
                >
                  <div className="flex items-center justify-between">
                    <span className="text-xs font-bold text-slate-200 uppercase truncate max-w-[180px]">
                      {t.title}
                    </span>
                    <span className={`text-[9px] px-2 py-0.5 rounded-full border font-bold uppercase ${statusColor(t.status)}`}>
                      {t.status}
                    </span>
                  </div>
                  <div className="text-[10px] text-slate-400 flex justify-between">
                    <span>Steps: {t.currentStep} / {t.totalSteps || 1}</span>
                    <span className="text-cyan-400">{Math.round((t.progress || 0) * 100)}%</span>
                  </div>
                </div>
              ))
            )}
          </div>
        </div>
      </div>
    </div>
  );
}
