import Editor from '@monaco-editor/react';
import * as React from 'react';
import { useState } from 'react';

interface Props {
  initial: Record<string, unknown> | null;
  onSave: (predicate: Record<string, unknown>) => void;
  saving?: boolean;
  schemaErrors?: string[];
  label?: string;
}

export function PredicateJsonEditor({
  initial,
  onSave,
  saving = false,
  schemaErrors,
  label = 'predicate json',
}: Props): React.JSX.Element {
  const [text, setText] = useState(
    initial === null ? '{\n  "kind": ""\n}' : JSON.stringify(initial, null, 2),
  );
  const [parseError, setParseError] = useState<string | null>(null);

  const handleSave = (): void => {
    try {
      const parsed: unknown = JSON.parse(text);
      if (parsed === null || typeof parsed !== 'object' || Array.isArray(parsed)) {
        setParseError('Predicate must be a JSON object');
        return;
      }
      setParseError(null);
      onSave(parsed as Record<string, unknown>);
    } catch (err) {
      setParseError(`Invalid JSON: ${(err as Error).message}`);
    }
  };

  return (
    <div className="flex flex-col gap-2" data-testid="predicate-json-editor">
      <div
        aria-label={label}
        className="min-h-[12rem] overflow-hidden rounded-md border border-border"
        data-testid="predicate-json-monaco"
      >
        <Editor
          height="12rem"
          language="json"
          value={text}
          onChange={(v) => setText(v ?? '')}
          options={{
            minimap: { enabled: false },
            scrollBeyondLastLine: false,
            fontSize: 12,
            lineNumbers: 'off',
            folding: false,
            wordWrap: 'on',
            tabSize: 2,
          }}
          theme="vs-dark"
        />
      </div>
      {parseError && (
        <p
          className="text-xs text-red-600"
          role="alert"
          data-testid="predicate-json-parse-error"
        >
          {parseError}
        </p>
      )}
      {schemaErrors && schemaErrors.length > 0 && (
        <ul
          className="space-y-0.5 text-xs text-red-600"
          role="alert"
          data-testid="predicate-json-schema-errors"
        >
          {schemaErrors.map((err, idx) => (
            <li key={idx}>{err}</li>
          ))}
        </ul>
      )}
      <button
        type="button"
        onClick={handleSave}
        disabled={saving}
        className="self-start rounded-md bg-primary px-3 py-1 text-xs text-primary-foreground hover:bg-primary/90 disabled:opacity-50"
        data-testid="predicate-json-save"
      >
        {saving ? 'Saving…' : 'Save'}
      </button>
    </div>
  );
}
