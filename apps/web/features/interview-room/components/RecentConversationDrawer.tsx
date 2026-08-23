"use client";

import { X } from "lucide-react";
import { useEffect, useRef } from "react";

import type { DeliveredConversationRow } from "../models/candidate-visible";
import { renderDeliveredText } from "./deliveredText";

type RecentConversationDrawerProps = {
  open: boolean;
  rows: DeliveredConversationRow[];
  onClose: () => void;
};

export function RecentConversationDrawer({ open, rows, onClose }: RecentConversationDrawerProps) {
  const closeButtonRef = useRef<HTMLButtonElement>(null);

  useEffect(() => {
    if (open) {
      closeButtonRef.current?.focus();
    }
  }, [open]);

  useEffect(() => {
    if (!open) {
      return undefined;
    }
    const handleKeyDown = (event: KeyboardEvent) => {
      if (event.key === "Escape") {
        onClose();
      }
    };
    window.addEventListener("keydown", handleKeyDown);
    return () => window.removeEventListener("keydown", handleKeyDown);
  }, [onClose, open]);

  return (
    <div className={open ? "drawer-layer drawer-layer-open" : "drawer-layer"} aria-hidden={!open}>
      <aside
        className="conversation-drawer"
        role="dialog"
        aria-modal="false"
        aria-labelledby="recent-conversation-title"
      >
        <div className="drawer-header">
          <div>
            <p className="panel-kicker">Delivered turns</p>
            <h2 id="recent-conversation-title">Recent conversation</h2>
          </div>
          <button
            ref={closeButtonRef}
            type="button"
            className="icon-button"
            onClick={onClose}
            aria-label="Close recent conversation"
          >
            <X size={17} aria-hidden="true" />
          </button>
        </div>
        <div className="conversation-list">
          {rows.map((row) => (
            <article key={row.id} className="conversation-row">
              <div className="conversation-meta">
                <span>{row.speaker}</span>
                <span>{row.deliveredAtLabel}</span>
              </div>
              <p>{renderDeliveredText(row.actualText)}</p>
            </article>
          ))}
        </div>
      </aside>
      <button
        type="button"
        className="drawer-scrim"
        aria-label="Close recent conversation overlay"
        onClick={onClose}
      />
    </div>
  );
}
