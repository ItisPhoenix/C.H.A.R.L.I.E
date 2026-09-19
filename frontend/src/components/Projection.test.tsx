import { fireEvent, render } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { Projection } from "./Projection";

describe("Projection drag affordance", () => {
  it("exposes pointer drag callbacks and reset for a pinned widget", () => {
    const onDragStart = vi.fn();
    const onDragMove = vi.fn();
    const onDragEnd = vi.fn();
    const onDragCancel = vi.fn();
    const onResetDrag = vi.fn();
    const view = render(<Projection
      item={{ id: "chat", importance: 86, treatment: "free", depth: "active", content: "Chat",
        draggable: true, pinnedPosition: { x: 20, y: 24 } }}
      onDragStart={onDragStart} onDragMove={onDragMove} onDragEnd={onDragEnd}
      onDragCancel={onDragCancel} onResetDrag={onResetDrag} />);
    const handle = view.container.querySelector(".projection-drag-handle")!;
    fireEvent.pointerDown(handle, { button: 0, pointerId: 1, clientX: 20, clientY: 24 });
    fireEvent.pointerMove(handle, { pointerId: 1, clientX: 34, clientY: 40 });
    fireEvent.pointerUp(handle, { pointerId: 1, clientX: 34, clientY: 40 });
    expect(onDragStart).toHaveBeenCalledOnce();
    expect(onDragMove).toHaveBeenCalledOnce();
    expect(onDragEnd).toHaveBeenCalledOnce();
    fireEvent.click(view.getByRole("button", { name: "Reset widget position" }));
    expect(onResetDrag).toHaveBeenCalledWith("chat");
    expect(onDragCancel).not.toHaveBeenCalled();
  });

  it("keeps interactive descendants outside the drag region", () => {
    const onDragStart = vi.fn();
    const view = render(<Projection
      item={{ id: "chat", importance: 86, treatment: "free", depth: "active", draggable: true,
        content: <><input aria-label="Message Charlie" /><button type="button">Send</button></> }}
      onDragStart={onDragStart} />);
    fireEvent.pointerDown(view.getByRole("textbox", { name: "Message Charlie" }), { button: 0, pointerId: 2 });
    fireEvent.pointerDown(view.getByRole("button", { name: "Send" }), { button: 0, pointerId: 3 });
    expect(onDragStart).not.toHaveBeenCalled();
    fireEvent.pointerDown(view.container.querySelector(".projection-drag-handle")!,
      { button: 0, pointerId: 4 });
    expect(onDragStart).toHaveBeenCalledOnce();
  });
});
