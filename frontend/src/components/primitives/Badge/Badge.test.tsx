import { describe, it, expect } from 'vitest';
import { render, screen } from '@testing-library/react';
import { Badge } from './Badge';

describe('Badge', () => {
  it('renders children text', () => {
    render(<Badge>live</Badge>);
    expect(screen.getByText('live')).toBeInTheDocument();
  });

  it('applies live variant class', () => {
    render(<Badge variant="live">live</Badge>);
    const el = screen.getByText('live');
    expect(el.className).toContain('bg-accent-live');
  });

  it('defaults to neutral variant when no variant prop', () => {
    render(<Badge>default</Badge>);
    const el = screen.getByText('default');
    expect(el.className).toContain('bg-panel');
    expect(el.className).toContain('text-fg-muted');
  });

  it('applies up and down variants', () => {
    render(
      <>
        <Badge variant="up">up</Badge>
        <Badge variant="down">down</Badge>
      </>,
    );
    expect(screen.getByText('up').className).toContain('text-positive');
    expect(screen.getByText('down').className).toContain('text-negative');
  });
});
