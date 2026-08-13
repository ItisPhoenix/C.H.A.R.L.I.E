import { useState, type ReactElement } from "react";
import { useCharlieStore, type RuntimeTask } from "../store/charlie";
import { Panel } from "./Panel";

type TaskTab = "active" | "completed";

function isTerminal(status: string): boolean {
  return status === "done" || status === "failed" || status === "cancelled";
}

function taskPercent(task: RuntimeTask): number | null {
  if (task.totalSteps <= 0) return null;
  return Math.round((Math.min(task.currentStep, task.totalSteps) / task.totalSteps) * 100);
}

export function Tasks(): ReactElement {
  const taskMap = useCharlieStore((state) => state.tasks);
  const [tab, setTab] = useState<TaskTab>("active");
  const tasks = Object.values(taskMap)
    .filter((task) => tab === "active" ? !isTerminal(task.status) : isTerminal(task.status))
    .slice(0, 4);

  return (
    <Panel id="tasks" title="Tasks">
      <div className="task-tabs">
        <button className={tab === "active" ? "is-active" : undefined} type="button" onClick={() => setTab("active")}>Active</button>
        <button className={tab === "completed" ? "is-active" : undefined} type="button" onClick={() => setTab("completed")}>Completed</button>
      </div>
      <div className="task-list">
        {tasks.length === 0 ? <p className="task-empty">No background tasks yet.</p> : tasks.map((task) => {
          const percent = taskPercent(task);
          return (
            <article className="task-row" key={task.id}>
              <span className="task-icon">{isTerminal(task.status) ? "▤" : "⌘"}</span>
              <div className="task-main">
                <div><strong>{task.title}</strong><span>{task.status}</span></div>
                <div className="task-progress"><i style={{ transform: `scaleX(${(percent ?? 0) / 100})` }} /></div>
              </div>
              <small>{percent == null ? "—" : `${percent}%`}</small>
            </article>
          );
        })}
      </div>
    </Panel>
  );
}
