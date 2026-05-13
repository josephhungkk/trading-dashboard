import { fireEvent, render, screen } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';

import { PredicateJsonEditor } from '@/features/alerts/PredicateJsonEditor';

describe('PredicateJsonEditor', () => {
  it('rejects malformed JSON with parse error and does not call onSave', () => {
    const onSave = vi.fn();
    render(<PredicateJsonEditor initial={null} onSave={onSave} />);

    fireEvent.change(screen.getByTestId('predicate-json-textarea'), {
      target: { value: '{not valid' },
    });
    fireEvent.click(screen.getByTestId('predicate-json-save'));

    expect(screen.getByTestId('predicate-json-parse-error').textContent).toMatch(
      /Invalid JSON/,
    );
    expect(onSave).not.toHaveBeenCalled();
  });

  it('parses object JSON and forwards to onSave', () => {
    const onSave = vi.fn();
    render(
      <PredicateJsonEditor
        initial={{ kind: 'price_threshold', symbol: 'AAPL', op: 'gt', value: 200 }}
        onSave={onSave}
      />,
    );

    fireEvent.click(screen.getByTestId('predicate-json-save'));

    expect(onSave).toHaveBeenCalledWith({
      kind: 'price_threshold',
      symbol: 'AAPL',
      op: 'gt',
      value: 200,
    });
  });

  it('renders schema_errors from server', () => {
    render(
      <PredicateJsonEditor
        initial={{ kind: 'price_threshold' }}
        onSave={vi.fn()}
        schemaErrors={['symbol is required', 'value is required']}
      />,
    );

    const errs = screen.getByTestId('predicate-json-schema-errors');
    expect(errs.textContent).toMatch(/symbol is required/);
    expect(errs.textContent).toMatch(/value is required/);
  });
});
