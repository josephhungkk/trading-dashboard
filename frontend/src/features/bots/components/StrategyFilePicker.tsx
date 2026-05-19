import * as React from 'react';
import { useQuery } from '@tanstack/react-query';
import { listStrategies } from '../../../services/bots/api';

interface Props {
  id?: string;
  value: string;
  onChange: (value: string) => void;
  disabled?: boolean;
}

export function StrategyFilePicker({ id, value, onChange, disabled }: Props): React.JSX.Element {
  const { data = [], isLoading } = useQuery({
    queryKey: ['strategies'],
    queryFn: listStrategies,
  });

  return (
    <select
      id={id}
      value={value}
      onChange={(e) => onChange(e.target.value)}
      disabled={disabled || isLoading}
      className="w-full rounded border border-border bg-background px-3 py-2 text-sm"
    >
      <option value="">Select strategy file…</option>
      {data.map((f) => (
        <option key={f.filename} value={f.filename}>
          {f.filename}
        </option>
      ))}
    </select>
  );
}
