import { useId, useRef, useState, type ReactElement, type ReactNode } from "react";

type Placement = "top" | "bottom";

/** A hover/focus tooltip that is also readable by assistive tech: the trigger is
 *  described by the bubble, so the content is announced rather than only drawn.
 *  Positioned with fixed coordinates measured from the trigger, so it escapes
 *  the `overflow: hidden` panels it is used inside. */
export function Tooltip({ content, children, placement = "top", className = "" }: { content: ReactNode; children: ReactElement; placement?: Placement; className?: string }) {
  const id = useId();
  const anchor = useRef<HTMLSpanElement>(null);
  const [position, setPosition] = useState<{ left: number; top: number } | null>(null);

  const show = () => {
    const rect = anchor.current?.firstElementChild?.getBoundingClientRect() ?? anchor.current?.getBoundingClientRect();
    if (!rect) return;
    setPosition({
      left: rect.left + rect.width / 2,
      top: placement === "top" ? rect.top - 8 : rect.bottom + 8,
    });
  };
  const hide = () => setPosition(null);

  return (
    <>
      <span
        ref={anchor}
        className={`tooltip-anchor ${className}`}
        aria-describedby={position ? id : undefined}
        onMouseEnter={show}
        onMouseLeave={hide}
        onFocus={show}
        onBlur={hide}
      >
        {children}
      </span>
      {position && (
        <span
          id={id}
          role="tooltip"
          className={`tooltip-bubble tooltip-bubble--${placement}`}
          style={{ left: position.left, top: position.top }}
        >
          {content}
        </span>
      )}
    </>
  );
}
