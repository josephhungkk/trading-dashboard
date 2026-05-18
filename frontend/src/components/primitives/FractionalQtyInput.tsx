import * as React from 'react';
import { exceedsPrecision } from '@/lib/decimal';

interface Props {
  value: string;
  onChange: (v: string) => void;
  step?: string;
  min?: string;
  max?: string;
  decimals?: number;
  placeholder?: string;
  disabled?: boolean;
}

export function FractionalQtyInput({
  value,
  onChange,
  step,
  min,
  max,
  decimals = 8,
  placeholder,
  disabled,
}: Props) {
  const [error, setError] = React.useState<string | null>(null);
  const handleChange = (e: React.ChangeEvent<HTMLInputElement>) => {
    setError(null);
    onChange(e.target.value);
  };
  const handleBlur = () => {
    if (value && exceedsPrecision(value, decimals)) {
      setError(`Max ${decimals} decimal places`);
    } else {
      setError(null);
    }
  };
  return (
    <div>
      <input
        type='number'
        role='spinbutton'
        value={value}
        onChange={handleChange}
        onBlur={handleBlur}
        step={step}
        min={min}
        max={max}
        placeholder={placeholder}
        disabled={disabled}
        className='w-full rounded border px-2 py-1 text-sm'
      />
      {error && <p className='mt-1 text-xs text-red-500'>{error} (precision exceeded)</p>}
    </div>
  );
}
