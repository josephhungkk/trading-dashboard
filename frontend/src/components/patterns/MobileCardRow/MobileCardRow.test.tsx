import { describe, it, expect, vi } from 'vitest';
import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MobileCardRow } from './MobileCardRow';

describe('MobileCardRow', () => {
  it('renders primary and metrics', () => {
    render(
      <MobileCardRow
        primary="AAPL"
        metrics={[
          { label: 'Last', value: '185.32' },
          { label: 'Chg', value: '+2.15' },
        ]}
      />,
    );
    expect(screen.getByText('AAPL')).toBeInTheDocument();
    expect(screen.getByText('Last:')).toBeInTheDocument();
    expect(screen.getByText('185.32')).toBeInTheDocument();
  });

  it('renders secondary when provided', () => {
    render(
      <MobileCardRow
        primary="AAPL"
        secondary="Apple Inc."
        metrics={[{ label: 'L', value: '1' }]}
      />,
    );
    expect(screen.getByText('Apple Inc.')).toBeInTheDocument();
  });

  it('fires onClick when clicked', async () => {
    const user = userEvent.setup();
    const onClick = vi.fn();
    render(
      <MobileCardRow
        primary="AAPL"
        metrics={[{ label: 'L', value: '1' }]}
        onClick={onClick}
      />,
    );
    await user.click(screen.getByRole('button'));
    expect(onClick).toHaveBeenCalledTimes(1);
  });
});
