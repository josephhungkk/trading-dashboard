import { describe, it, expect } from 'vitest';
import { render, screen } from '@testing-library/react';
import { NumericCell } from './NumericCell';

describe('NumericCell', () => {
  it('renders formatted number with default digits=2', () => {
    render(<NumericCell value={1234.5} />);
    expect(screen.getByText(/1,234\.50/)).toBeInTheDocument();
  });

  it('renders currency with USD symbol', () => {
    render(<NumericCell value={1000} format="currency" currency="USD" />);
    // Locale-agnostic: accept either "$1,000.00" or "US$1,000.00"
    expect(screen.getByText(/\$1,000\.00/)).toBeInTheDocument();
  });

  it('renders percent with % symbol', () => {
    render(<NumericCell value={0.05} format="percent" digits={2} />);
    expect(screen.getByText(/5\.00%/)).toBeInTheDocument();
  });

  it('renders em-dash for null/undefined/NaN', () => {
    const { rerender } = render(<NumericCell value={null} />);
    expect(screen.getByText('—')).toBeInTheDocument();
    rerender(<NumericCell value={undefined} />);
    expect(screen.getByText('—')).toBeInTheDocument();
    rerender(<NumericCell value={NaN} />);
    expect(screen.getByText('—')).toBeInTheDocument();
  });

  it('applies emphasis=up tone class', () => {
    render(<NumericCell value={42} emphasis="up" />);
    const span = screen.getByText(/42/);
    expect(span.className).toContain('text-positive');
  });
});
