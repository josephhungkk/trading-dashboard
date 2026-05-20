import * as React from "react";
import { Checkbox } from "@/components/primitives/Checkbox";

interface Props {
  message: string;
  onAcknowledge: (acked: boolean) => void;
}

export function BbWarningBanner({ message, onAcknowledge }: Props): React.JSX.Element {
  const [checked, setChecked] = React.useState(false);

  function handleChange(v: boolean): void {
    setChecked(v);
    onAcknowledge(v);
  }

  return (
    <div className="rounded-md border border-yellow-400 bg-yellow-50 dark:bg-yellow-950/20 p-3 space-y-2">
      <p className="text-sm text-yellow-800 dark:text-yellow-200">
        <strong>HMRC b&amp;b rule applies:</strong> {message}
      </p>
      <label htmlFor="bb-ack" className="flex items-center gap-2 text-sm cursor-pointer">
        <Checkbox id="bb-ack" checked={checked} onCheckedChange={handleChange} />
        I understand this acquisition will be matched against the prior disposal
      </label>
    </div>
  );
}
