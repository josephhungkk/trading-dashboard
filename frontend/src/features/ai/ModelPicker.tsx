import * as React from 'react';

import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '@/components/primitives/Select';
import type { AICapability } from '@/services/ai/types';

export interface ModelPickerProps {
  value: AICapability;
  onChange: (cap: AICapability) => void;
  disabled?: boolean;
}

export const AI_CAPABILITY_OPTIONS: readonly {
  value: AICapability;
  label: string;
}[] = [
  { value: 'CODING', label: 'Coding' },
  { value: 'REASONING', label: 'Reasoning' },
  { value: 'STRUCTURED_OUTPUT', label: 'Structured output' },
  { value: 'LONG_CONTEXT', label: 'Long context' },
  { value: 'REALTIME_SENTIMENT', label: 'Realtime sentiment' },
  { value: 'NUMERICAL', label: 'Numerical analysis' },
  { value: 'BULK_CHEAP', label: 'Bulk cheap' },
  { value: 'LOCAL_ONLY', label: 'Local only (NUC)' },
];

export function ModelPicker({
  value,
  onChange,
  disabled = false,
}: ModelPickerProps): React.JSX.Element {
  return (
    <Select
      value={value}
      onValueChange={(nextValue) => onChange(nextValue as AICapability)}
      disabled={disabled}
    >
      <SelectTrigger aria-label="AI capability" className="min-h-11">
        <SelectValue />
      </SelectTrigger>
      <SelectContent>
        {AI_CAPABILITY_OPTIONS.map((option) => (
          <SelectItem key={option.value} value={option.value}>
            {option.label}
          </SelectItem>
        ))}
      </SelectContent>
    </Select>
  );
}
