"use client";

import { X } from "lucide-react";
import { useEffect, useRef } from "react";

type EndInterviewDialogProps = {
  open: boolean;
  onCancel: () => void;
  onConfirm: () => void;
};

export function EndInterviewDialog({ open, onCancel, onConfirm }: EndInterviewDialogProps) {
  const cancelButtonRef = useRef<HTMLButtonElement>(null);

  useEffect(() => {
    if (open) {
      cancelButtonRef.current?.focus();
    }
  }, [open]);

  useEffect(() => {
    if (!open) {
      return undefined;
    }
    const handleKeyDown = (event: KeyboardEvent) => {
      if (event.key === "Escape") {
        onCancel();
      }
    };
    window.addEventListener("keydown", handleKeyDown);
    return () => window.removeEventListener("keydown", handleKeyDown);
  }, [onCancel, open]);

  if (!open) {
    return null;
  }

  return (
    <div className="dialog-layer">
      <div className="dialog-panel" role="dialog" aria-modal="true" aria-labelledby="end-dialog-title">
        <button type="button" className="dialog-close" onClick={onCancel} aria-label="Close end interview dialog">
          <X size={18} aria-hidden="true" />
        </button>
        <h2 id="end-dialog-title">End this interview?</h2>
        <p>Your current demo session will stop.</p>
        <div className="dialog-actions">
          <button ref={cancelButtonRef} type="button" className="secondary-button" onClick={onCancel}>
            Continue interview
          </button>
          <button type="button" className="danger-button" onClick={onConfirm}>
            End interview
          </button>
        </div>
      </div>
    </div>
  );
}
