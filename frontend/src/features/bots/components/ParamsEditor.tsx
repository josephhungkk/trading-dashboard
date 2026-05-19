import * as React from 'react';

interface Props {
  value: Record<string, unknown>;
  schema: Record<string, unknown> | null;
  onChange: (value: Record<string, unknown>) => void;
  disabled?: boolean;
}

export function ParamsEditor({ value, schema, onChange, disabled }: Props): React.JSX.Element {
  const [raw, setRaw] = React.useState(() => JSON.stringify(value, null, 2));
  const [error, setError] = React.useState<string | null>(null);

  const handleChange = (text: string) => {
    setRaw(text);
    try {
      const parsed = JSON.parse(text) as Record<string, unknown>;
      setError(null);
      onChange(parsed);
    } catch {
      setError('Invalid JSON');
    }
  };

  const properties =
    schema != null &&
    typeof schema === 'object' &&
    'properties' in schema &&
    schema.properties != null
      ? (schema.properties as Record<string, { type?: string; description?: string }>)
      : null;

  return (
    <div className="space-y-2">
      {properties != null && (
        <p className="text-xs text-muted-foreground">
          Fields:{' '}
          {Object.entries(properties)
            .map(([k, v]) => `${k}${v.type ? ` (${v.type})` : ''}`)
            .join(', ')}
        </p>
      )}
      <textarea
        value={raw}
        onChange={(e) => handleChange(e.target.value)}
        disabled={disabled}
        rows={8}
        spellCheck={false}
        className="w-full rounded border border-border bg-background px-3 py-2 font-mono text-xs"
      />
      {error != null && <p className="text-xs text-destructive">{error}</p>}
    </div>
  );
}
