import { useEffect, useMemo, useState, type FormEvent, type ReactElement } from "react";
import { Panel } from "./Panel";

const WEEKDAYS = ["S", "M", "T", "W", "T", "F", "S"];

function monthDays(date: Date): Array<number | null> {
  const firstDay = new Date(date.getFullYear(), date.getMonth(), 1).getDay();
  const count = new Date(date.getFullYear(), date.getMonth() + 1, 0).getDate();
  return [...Array<null>(firstDay).fill(null), ...Array.from({ length: count }, (_, index) => index + 1)];
}

export function Calendar(): ReactElement {
  const today = useMemo(() => new Date(), []);
  const day = today.toISOString().slice(0, 10);
  const month = today.toLocaleDateString("en-US", { month: "long", year: "numeric" });
  const [events, setEvents] = useState<Array<{ id: string; title: string; start_at: string }>>([]);
  const [title, setTitle] = useState("");
  const [startAt, setStartAt] = useState("");
  const [adding, setAdding] = useState(false);

  useEffect(() => {
    void fetch(`/api/calendar/events?day=${day}`)
      .then((response) => response.ok ? response.json() as Promise<{ events?: Array<{ id: string; title: string; start_at: string }> }> : Promise.reject(new Error("Calendar unavailable")))
      .then((data) => setEvents(Array.isArray(data.events) ? data.events : []))
      .catch(() => setEvents([]));
  }, [day]);

  async function addReminder(event: FormEvent): Promise<void> {
    event.preventDefault();
    if (!title.trim() || !startAt) return;
    const response = await fetch("/api/calendar/events", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ title, start_at: new Date(startAt).toISOString(), reminder_at: new Date(startAt).toISOString() }),
    });
    if (!response.ok) return;
    const created = await response.json() as { id: string; title: string; start_at: string };
    setEvents((current) => [...current, created].sort((left, right) => left.start_at.localeCompare(right.start_at)));
    setTitle("");
    setStartAt("");
    setAdding(false);
  }

  async function deleteReminder(id: string): Promise<void> {
    const response = await fetch(`/api/calendar/events/${encodeURIComponent(id)}`, { method: "DELETE" });
    if (response.ok) setEvents((current) => current.filter((event) => event.id !== id));
  }

  async function editReminder(event: { id: string; title: string; start_at: string }): Promise<void> {
    const nextTitle = window.prompt("Reminder title", event.title);
    if (!nextTitle?.trim()) return;
    const response = await fetch(`/api/calendar/events/${encodeURIComponent(event.id)}`, {
      method: "PUT", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ title: nextTitle.trim() }),
    });
    if (response.ok) setEvents((current) => current.map((item) => item.id === event.id ? { ...item, title: nextTitle.trim() } : item));
  }

  return (
    <Panel id="calendar" title="Calendar">
      <div className="calendar-layout">
        <div className="calendar-month">
          <div className="month-head"><strong>{month}</strong></div>
          <div className="weekdays">{WEEKDAYS.map((day, index) => <span key={`${day}-${index}`}>{day}</span>)}</div>
          <div className="calendar-days">{monthDays(today).map((day, index) => <span className={day === today.getDate() ? "is-today" : undefined} key={index}>{day ?? ""}</span>)}</div>
        </div>
        <div className="calendar-agenda"><strong>Today</strong>{events.length === 0 ? <p>No calendar events reported.</p> : events.map((event) => <p key={event.id}><time>{new Date(event.start_at).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" })}</time><span>{event.title}</span><button type="button" aria-label={`Edit ${event.title}`} onClick={() => void editReminder(event)}>Edit</button><button type="button" aria-label={`Delete ${event.title}`} onClick={() => void deleteReminder(event.id)}>Delete</button></p>)}<button type="button" className="calendar-add" onClick={() => setAdding((current) => !current)}>Add reminder</button>{adding ? <form className="calendar-form" onSubmit={(event) => void addReminder(event)}><input aria-label="Reminder title" placeholder="Title" value={title} onChange={(event) => setTitle(event.target.value)} /><input aria-label="Reminder time" type="datetime-local" value={startAt} onChange={(event) => setStartAt(event.target.value)} /><button type="submit">Save</button></form> : null}</div>
      </div>
    </Panel>
  );
}
