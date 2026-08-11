import type { ReactElement } from "react";
import { useParams } from "react-router-dom";
import { useCharlieStore } from "../store/charlie";
import { Widget } from "./base/Widget";
import { Workspace } from "./base/Workspace";
import { Notification } from "./base/Notification";
import { Modal, ModalField } from "../components/Modal";
import { ToolApprovalDialog } from "../components/ToolApprovalDialog";

// One window renders exactly one surface, resolved by its id across the 4 presentation maps.
export function SurfaceRoute(): ReactElement | null {
  const { surfaceId } = useParams<{ surfaceId: string }>();
  const widget = useCharlieStore((s) => (surfaceId ? s.widgets[surfaceId] : undefined));
  const modal = useCharlieStore((s) => (surfaceId ? s.modals[surfaceId] : undefined));
  const workspace = useCharlieStore((s) => (surfaceId ? s.workspaces[surfaceId] : undefined));
  const notification = useCharlieStore((s) => (surfaceId ? s.notifications[surfaceId] : undefined));
  const activeToolApproval = useCharlieStore((s) => s.activeToolApproval);

  if (modal) {
    if (activeToolApproval) return <ToolApprovalDialog />;
    return (
      <Modal labelledBy="surface-modal-title">
        <h3 id="surface-modal-title" className="text-lg font-semibold text-[var(--color-text-primary)]">
          Charlie
        </h3>
        <ModalField label="Why">{modal.rationale}</ModalField>
      </Modal>
    );
  }
  if (workspace) return <Workspace spec={workspace} />;
  if (widget) return <Widget spec={widget} />;
  if (notification) return <Notification spec={notification} />;
  return null;
}
