import type { ReactElement } from "react";
import { Panel } from "./Panel";

const WEEKDAYS = ["S", "M", "T", "W", "T", "F", "S"];

function monthDays(date: Date): Array<number | null> {
  const firstDay = new Date(date.getFullYear(), date.getMonth(), 1).getDay();
  const count = new Date(date.getFullYear(), date.getMonth() + 1, 0).getDate();
  return [...Array<null>(firstDay).fill(null), ...Array.from({ length: count }, (_, index) => index + 1)];
}

export function Calendar(): ReactElement {
  const today = new Date();
  const month = today.toLocaleDateString("en-US", { month: "long", year: "numeric" });

  return (
    <Panel id="calendar" title="Calendar">
      <div className="calendar-layout">
        <div className="calendar-month">
          <div className="month-head"><strong>{month}</strong></div>
          <div className="weekdays">{WEEKDAYS.map((day, index) => <span key={`${day}-${index}`}>{day}</span>)}</div>
          <div className="calendar-days">{monthDays(today).map((day, index) => <span className={day === today.getDate() ? "is-today" : undefined} key={index}>{day ?? ""}</span>)}</div>
        </div>
        <div className="calendar-agenda"><strong>Today</strong><p>No calendar events reported.</p></div>
      </div>
    </Panel>
  );
}
