import type { ReactNode } from "react";

export function renderDeliveredText(text: string): ReactNode[] {
  return text.split(/(`[^`]+`)/g).map((part, index) => {
    if (part.startsWith("`") && part.endsWith("`")) {
      return (
        <code key={`${part}-${index}`} className="delivered-inline-code">
          {part.slice(1, -1)}
        </code>
      );
    }
    return part;
  });
}
