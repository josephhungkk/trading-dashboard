import * as React from 'react';

import { PredicateJsonEditor } from '@/features/alerts/PredicateJsonEditor';

interface Props {
  partialPredicate: Record<string, unknown> | null;
  suggestions: string[];
  onSave: (predicate: Record<string, unknown>) => void;
  saving?: boolean;
  schemaErrors?: string[];
}

export function ParseFailedEditor({
  partialPredicate,
  suggestions,
  onSave,
  saving,
  schemaErrors,
}: Props): React.JSX.Element {
  return (
    <section
      className="flex flex-col gap-3 rounded-md border border-amber-300 bg-amber-50 p-4"
      data-testid="parse-failed-editor"
    >
      <header>
        <h2 className="text-sm font-semibold uppercase tracking-wide text-amber-900">
          Parse failed
        </h2>
        <p className="text-xs text-amber-900">
          Edit the predicate JSON manually, or revise the rule text and try again.
        </p>
      </header>
      {suggestions.length > 0 && (
        <ul
          className="list-disc space-y-0.5 pl-5 text-xs text-amber-900"
          data-testid="parse-failed-suggestions"
        >
          {suggestions.map((s, idx) => (
            <li key={idx}>{s}</li>
          ))}
        </ul>
      )}
      <PredicateJsonEditor
        initial={partialPredicate}
        onSave={onSave}
        saving={saving ?? false}
        schemaErrors={schemaErrors ?? []}
        label="predicate json (parse failed)"
      />
    </section>
  );
}
