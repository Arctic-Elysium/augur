import { useEffect, useRef } from "react";

/** Hand-rolled rather than a dependency.
 *
 * Takes focus on the confirm button so a keyboard user is already on the thing
 * they came to press, closes on Escape, and traps nothing else - a dialog that
 * fights the browser is worse than one that does not exist. */
export function ConfirmDialog({
  title,
  body,
  label,
  destructive,
  run,
  onClose,
}: {
  title: string;
  body: string;
  label: string;
  destructive?: boolean;
  run: () => void;
  onClose: () => void;
}) {
  const confirmRef = useRef<HTMLButtonElement>(null);

  useEffect(() => {
    confirmRef.current?.focus();
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") onClose();
    };
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, [onClose]);

  return (
    <div className="scrim" onClick={onClose}>
      <div
        className="dialog"
        role="dialog"
        aria-modal="true"
        aria-labelledby="confirm-title"
        onClick={(e) => e.stopPropagation()}
      >
        <h2 id="confirm-title" className="dialog__title">
          {title}
        </h2>
        <p className="dialog__body">{body}</p>
        <div className="dialog__actions">
          <button className="btn" onClick={onClose}>
            Cancel
          </button>
          <button
            ref={confirmRef}
            className={`btn ${destructive ? "btn--bad" : "btn--go"}`}
            onClick={() => {
              run();
              onClose();
            }}
          >
            {label}
          </button>
        </div>
      </div>
    </div>
  );
}
