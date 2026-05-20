import * as React from "react";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/primitives/Select";

interface Props {
  value: number;
  onChange: (year: number) => void;
}

function taxYearLabel(year: number): string {
  return `${year}/${String(year + 1).slice(-2)}`;
}

export function TaxYearSelector({ value, onChange }: Props): React.JSX.Element {
  const now = new Date();
  const currentYear = now.getMonth() >= 3 ? now.getFullYear() : now.getFullYear() - 1;
  const years = Array.from({ length: 4 }, (_, i) => currentYear - i);

  return (
    <Select value={String(value)} onValueChange={(v) => onChange(Number(v))}>
      <SelectTrigger className="w-32">
        <SelectValue>{taxYearLabel(value)}</SelectValue>
      </SelectTrigger>
      <SelectContent>
        {years.map((y) => (
          <SelectItem key={y} value={String(y)}>
            {taxYearLabel(y)}
          </SelectItem>
        ))}
      </SelectContent>
    </Select>
  );
}
