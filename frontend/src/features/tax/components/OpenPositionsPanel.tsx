import * as React from "react";

export function OpenPositionsPanel(): React.JSX.Element {
  return (
    <div className="p-3 border rounded-lg bg-muted/30 text-sm text-muted-foreground">
      Open positions are informational only — unrealised gains/losses are excluded
      from CGT calculations until disposal.
    </div>
  );
}
