import * as React from 'react';

interface Props {
  predicate: Record<string, unknown> | null;
}

function isComposite(kind: unknown): kind is 'composite_and' | 'composite_or' {
  return kind === 'composite_and' || kind === 'composite_or';
}

function PredicateNode({ node }: { node: Record<string, unknown> }): React.JSX.Element {
  const kind = node.kind;
  if (isComposite(kind)) {
    const children = Array.isArray(node.children) ? node.children : [];
    const label = kind === 'composite_and' ? 'ALL of' : 'ANY of';
    return (
      <details
        className="ml-2 border-l border-border pl-3 py-1"
        data-testid={`predicate-composite-${kind}`}
        open
      >
        <summary className="cursor-pointer text-sm font-semibold uppercase tracking-wide text-muted-foreground">
          {label}
        </summary>
        <ul className="mt-1 space-y-1">
          {children.map((child, idx) => (
            <li key={idx}>
              <PredicateNode node={child as Record<string, unknown>} />
            </li>
          ))}
        </ul>
      </details>
    );
  }
  return (
    <div
      className="flex flex-wrap items-center gap-2 rounded-md bg-muted/50 px-3 py-1.5 text-sm"
      data-testid={`predicate-leaf-${String(kind)}`}
    >
      <span className="font-mono text-xs uppercase text-muted-foreground">
        {String(kind ?? 'unknown')}
      </span>
      <span className="text-xs text-foreground">
        {Object.entries(node)
          .filter(([k]) => k !== 'kind')
          .map(([k, v]) => `${k}=${JSON.stringify(v)}`)
          .join(' · ')}
      </span>
    </div>
  );
}

export function PredicateVisualiser({ predicate }: Props): React.JSX.Element {
  if (predicate === null) {
    return (
      <div
        className="rounded-md border border-dashed border-border p-3 text-sm text-muted-foreground"
        data-testid="predicate-empty"
      >
        No predicate
      </div>
    );
  }
  return (
    <section
      className="rounded-md border border-border bg-panel p-3"
      data-testid="predicate-visualiser"
    >
      <PredicateNode node={predicate} />
    </section>
  );
}
