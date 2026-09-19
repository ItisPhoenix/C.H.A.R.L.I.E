import { useEffect, useMemo, useRef, useState } from "react";
import { CharlieCore, coreStates, type CoreState } from "./components/CharlieCore";
import { HudControls, type HudControlsProps } from "./components/HudControls";
import { Projection, type ProjectionData, type ProjectionInteractionProps } from "./components/Projection";
import { fixtureFromSearch, fixtureNames, makeFixture, type FixtureName } from "./fixtures";
import { mapRuntimeToScene } from "./runtime/mapper";
import { useRuntime } from "./runtime/client";
import { useSpatialLayout, type SpatialResetRequest } from "./useSpatialLayout";

export function SpatialScene({ items, state, controls }: {
  items: ProjectionData[];
  state: CoreState;
  controls?: HudControlsProps;
}) {
  const canvas = useRef<HTMLDivElement>(null);
  const [displayed, setDisplayed] = useState(items);
  const [pinnedPositions, setPinnedPositions] = useState<Record<string, { x: number; y: number }>>({});
  const [draggingId, setDraggingId] = useState<string | null>(null);
  const resetCounter = useRef(0);
  const [resetRequest, setResetRequest] = useState<SpatialResetRequest | null>(null);
  const dragRef = useRef<{
    id: string;
    pointerId: number;
    element: HTMLElement;
    originX: number;
    originY: number;
    x: number;
    y: number;
    startClientX: number;
    startClientY: number;
    scale: number;
    width: number;
    height: number;
  } | null>(null);
  useEffect(() => {
    // Keep removed items mounted through their opacity exit; surviving keys retain state.
    const active = new Set(items.map(item => item.id));
    if (matchMedia("(prefers-reduced-motion: reduce)").matches) {
      setDisplayed(items);
      return;
    }
    setDisplayed(current => [...items, ...current.filter(item => !active.has(item.id))]);
    const timer = setTimeout(() => setDisplayed(items), 180);
    return () => clearTimeout(timer);
  }, [items]);
  const decorate = (item: ProjectionData) => {
    const pinnedPosition = pinnedPositions[item.id];
    return pinnedPosition ? { ...item, pinnedPosition } : item;
  };
  const placedItems = useMemo(() => items.map(decorate), [items, pinnedPositions]);
  const displayedItems = useMemo(() => displayed.map(decorate), [displayed, pinnedPositions]);
  const beginDrag: ProjectionInteractionProps["onDragStart"] = (id, event) => {
    if (event.button !== 0 || draggingId) return;
    const element = event.currentTarget.closest<HTMLElement>(".projection");
    if (!element || element.dataset.leaving) return;
    const scale = Number(element.dataset.layoutScale || "1") || 1;
    const originX = Number(element.dataset.x || "0");
    const originY = Number(element.dataset.y || "0");
    const width = Number(element.dataset.measuredWidth || element.offsetWidth);
    const height = Number(element.dataset.measuredHeight || element.offsetHeight);
    dragRef.current = {
      id, pointerId: event.pointerId, element, originX, originY, x: originX, y: originY,
      startClientX: event.clientX, startClientY: event.clientY, scale, width, height,
    };
    element.dataset.dragging = "true";
    element.getAnimations?.().forEach(animation => animation.cancel());
    event.currentTarget.setPointerCapture?.(event.pointerId);
    setDraggingId(id);
    event.preventDefault();
  };
  const dragPositionIsSafe = (drag: NonNullable<typeof dragRef.current>, x: number, y: number) => {
    const root = canvas.current;
    if (!root) return true;
    const core = root.querySelector<HTMLElement>('[data-spatial-id="charlie"]');
    if (!core) return true;
    const coreX = Number(core.dataset.x || "0");
    const coreY = Number(core.dataset.y || "0");
    const coreWidth = Number(core.dataset.measuredWidth || core.offsetWidth) * (Number(core.dataset.layoutScale || "1") || 1);
    const coreHeight = Number(core.dataset.measuredHeight || core.offsetHeight) * (Number(core.dataset.layoutScale || "1") || 1);
    const gapValue = parseFloat(getComputedStyle(root).getPropertyValue("--spatial-gap"));
    const clearance = Math.max(Number.isFinite(gapValue) ? gapValue : 28, Math.max(coreWidth, coreHeight) * .6);
    const width = drag.width * drag.scale;
    const height = drag.height * drag.scale;
    return !(x < coreX + coreWidth + clearance && x + width + clearance > coreX &&
      y < coreY + coreHeight + clearance && y + height + clearance > coreY);
  };
  const moveDrag: ProjectionInteractionProps["onDragMove"] = (id, event) => {
    const drag = dragRef.current;
    const root = canvas.current;
    if (!drag || !root || drag.id !== id || drag.pointerId !== event.pointerId) return;
    const width = drag.width * drag.scale;
    const height = drag.height * drag.scale;
    const maxX = Math.max(0, root.clientWidth - width);
    const maxY = Math.max(0, root.clientHeight - height);
    const nextX = Math.max(0, Math.min(maxX, drag.originX + event.clientX - drag.startClientX));
    const nextY = Math.max(0, Math.min(maxY, drag.originY + event.clientY - drag.startClientY));
    if (dragPositionIsSafe(drag, nextX, nextY)) {
      drag.x = nextX;
      drag.y = nextY;
    }
    drag.element.style.transform = `translate(${drag.x}px, ${drag.y}px) scale(${drag.scale})`;
    drag.element.dataset.dragX = String(drag.x);
    drag.element.dataset.dragY = String(drag.y);
    event.preventDefault();
  };
  const endDrag: ProjectionInteractionProps["onDragEnd"] = (id, event) => {
    const drag = dragRef.current;
    if (!drag || drag.id !== id || drag.pointerId !== event.pointerId) return;
    setPinnedPositions(current => ({ ...current, [id]: { x: drag.x, y: drag.y } }));
    delete drag.element.dataset.dragging;
    delete drag.element.dataset.dragX;
    delete drag.element.dataset.dragY;
    try { event.currentTarget.releasePointerCapture?.(event.pointerId); } catch { /* pointer already released */ }
    dragRef.current = null;
    setDraggingId(null);
  };
  const cancelDrag: ProjectionInteractionProps["onDragCancel"] = (id, event) => {
    const drag = dragRef.current;
    if (!drag || drag.id !== id || drag.pointerId !== event.pointerId) return;
    drag.element.style.transform = `translate(${drag.originX}px, ${drag.originY}px) scale(${drag.scale})`;
    delete drag.element.dataset.dragging;
    dragRef.current = null;
    setDraggingId(null);
  };
  const resetDrag = (id: string) => {
    setResetRequest({ id, token: ++resetCounter.current });
    setPinnedPositions(current => {
      if (!current[id]) return current;
      const next = { ...current };
      delete next[id];
      return next;
    });
  };
  const metadata = useMemo(() => [
    ...placedItems,
    { id: "charlie", importance: 65, core: true },
  ], [placedItems]);
  useSpatialLayout(canvas, metadata, displayed.length, draggingId, resetRequest);
  return (
    <main className="charlie-scene" aria-label="Charlie environment" data-active={items.length > 0}
      data-immersive={items.some(item => item.immersive) || undefined}>
      <div className="environment-layer" aria-hidden="true">
        <div className="environment-calibration">
          <span className="environment-ruler" />
          <svg className="environment-notch" viewBox="0 0 240 32" aria-hidden="true">
            <path d="M0 7H72L94 28H146L168 7H240" />
            <path d="M30 7H210" />
          </svg>
        </div>
      </div>
      <div className="scene-viewport" tabIndex={0} aria-label="Spatial workspace">
        <div className="spatial-canvas" ref={canvas}>
          {displayedItems.map(item => <Projection key={item.id} item={item}
            leaving={!items.some(active => active.id === item.id)}
            onDragStart={beginDrag} onDragMove={moveDrag} onDragEnd={endDrag}
            onDragCancel={cancelDrag} onResetDrag={resetDrag} />)}
          <div className="spatial-item core-anchor" data-spatial-id="charlie">
            <CharlieCore state={state} />
          </div>
        </div>
      </div>
      {controls ? <HudControls {...controls} /> : <div className="utility-strip" data-utility-strip aria-hidden="true">
        <svg viewBox="0 0 132 22" role="presentation">
          <g className="utility-icon utility-icon--signal">
            <circle cx="9" cy="11" r="6" />
            <path d="M9 7.5v7M5.5 11h7" />
          </g>
          <path className="utility-divider" d="M26 4v14" />
          <g className="utility-icon">
            <rect x="39" y="6" width="11" height="9" rx="1" />
            <path d="M42 18h5" />
          </g>
          <path className="utility-divider" d="M64 4v14" />
          <g className="utility-icon">
            <rect x="77" y="7" width="12" height="8" rx="1" />
            <path d="M80 5v2M86 5v2M83 15v3" />
          </g>
          <path className="utility-divider" d="M103 4v14" />
          <g className="utility-icon utility-icon--note">
            <path d="M116 6v10.5a2.5 2.5 0 1 0 2-2.45V8l7-2v7.5" />
          </g>
        </svg>
        <span className="utility-status"><i /><i /><i /></span>
      </div>}
    </main>
  );
}

function FixtureApp() {
  const params = new URLSearchParams(location.search);
  const [fixture, setFixture] = useState<FixtureName>(() => fixtureFromSearch(location.search));
  const [expanded, setExpanded] = useState(false);
  const [extraTask, setExtraTask] = useState(false);
  const [approved, setApproved] = useState(false);
  const [stateOverride, setStateOverride] = useState<CoreState | "">("");
  const paired = params.has("pair");
  const demo = params.has("fixture") && !params.has("capture");
  const data = useMemo(() => makeFixture(fixture, {
    expanded, extraTask, approved, paired,
    expand: () => setExpanded(value => !value),
    toggleTask: () => setExtraTask(value => !value),
    approve: () => setApproved(true),
  }), [fixture, expanded, extraTask, approved, paired]);
  return <>
    <SpatialScene items={data.items} state={stateOverride || data.state} />
    {demo && <details className="demo-tools">
      <summary>Demonstration data <span aria-hidden="true">· {fixture}</span></summary>
      <p>Local samples. No live services, voice capture, or file writes.</p>
      <label>Dataset <select value={fixture} onChange={event => {
        setFixture(event.target.value as FixtureName); setExpanded(false);
        setExtraTask(false); setApproved(false); setStateOverride("");
      }}>{fixtureNames.map(name => <option key={name}>{name}</option>)}</select></label>
      <label>Core state <select value={stateOverride} onChange={event => setStateOverride(event.target.value as CoreState | "")}>
        <option value="">Follow sample</option>
        {coreStates.map(state => <option key={state}>{state}</option>)}
      </select></label>
    </details>}
  </>;
}

function RuntimeApp() {
  const { runtime, client, actions } = useRuntime();
  const [pttPressed, setPttPressed] = useState(false);
  const [controlStatus, setControlStatus] = useState("");
  const feedbackTimer = useRef<ReturnType<typeof setTimeout> | null>(null);
  useEffect(() => () => {
    if (feedbackTimer.current) clearTimeout(feedbackTimer.current);
  }, []);
  const scene = useMemo(() => mapRuntimeToScene(runtime, actions), [runtime, actions]);
  const connectionUnavailable = runtime.connection !== "connected";
  const truthSubsystems = runtime.runtimeTruth?.subsystems as Record<string, unknown> | undefined;
  const healthSubsystems = runtime.subsystemHealth.subsystems as Record<string, unknown> | undefined;
  const voiceCapture = (truthSubsystems?.voice_capture ?? truthSubsystems?.voice ??
    healthSubsystems?.voice_capture ?? healthSubsystems?.voice ??
    runtime.subsystemHealth.voice_capture ?? runtime.subsystemHealth.voice) as Record<string, unknown> | undefined;
  const voiceStatus = typeof voiceCapture?.status === "string" ? voiceCapture.status : "";
  const pttDisabled = connectionUnavailable || runtime.micState.available === false ||
    (voiceStatus !== "" && !["running", "ready"].includes(voiceStatus));
  const reportControl = (sent: boolean, success: string, failure: string) => {
    if (feedbackTimer.current) clearTimeout(feedbackTimer.current);
    setControlStatus(sent ? success : failure);
    feedbackTimer.current = setTimeout(() => setControlStatus(""), 900);
  };
  const controls: HudControlsProps = {
    pttDisabled,
    conversationDisabled: connectionUnavailable,
    pressed: pttPressed,
    status: controlStatus || (connectionUnavailable ? `Runtime ${runtime.connection}.` : ""),
    onPttStart: () => {
      const sent = client.ptt("start");
      setPttPressed(sent);
      reportControl(sent, "Listening…", "Runtime link unavailable; PTT was not sent.");
    },
    onPttStop: () => {
      const sent = client.ptt("stop");
      setPttPressed(false);
      reportControl(sent, "Ready.", "Runtime link unavailable; PTT release was not sent.");
    },
    onPttCancel: () => {
      const sent = client.ptt("cancel");
      setPttPressed(false);
      reportControl(sent, "PTT cancelled.", "Runtime link unavailable; PTT cancel was not sent.");
    },
    onOpenConversation: () => {
      const sent = client.openConversation();
      reportControl(sent, "Opening conversation…", "Runtime link unavailable; conversation command was not sent.");
    },
  };
  return <SpatialScene items={scene.items} state={scene.state} controls={controls} />;
}

export function App() {
  return new URLSearchParams(location.search).has("fixture") ? <FixtureApp /> : <RuntimeApp />;
}
