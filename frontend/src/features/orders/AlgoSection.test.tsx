// frontend/src/features/orders/AlgoSection.test.tsx
import { render, screen, fireEvent, waitFor } from '@testing-library/react';
import { vi, describe, it, expect, beforeEach } from 'vitest';

vi.mock('@/services/algo/api', () => ({
  getAlgoCapabilities: vi.fn(),
}));

import { AlgoSection } from './AlgoSection';
import { getAlgoCapabilities } from '@/services/algo/api';

const mockGetCap = vi.mocked(getAlgoCapabilities);

beforeEach(() => {
  vi.clearAllMocks();
});

describe('AlgoSection', () => {
  it('renders collapsed chip with Off label', async () => {
    mockGetCap.mockResolvedValueOnce({
      strategies: [
        {
          strategy: 'TWAP',
          params: [
            { name: 'start_time', type: 'time', required: true },
            { name: 'end_time', type: 'time', required: true },
          ],
        },
      ],
    });
    render(
      <AlgoSection
        brokerId="ibkr"
        assetClass="STOCK"
        onAlgoChange={vi.fn()}
      />,
    );
    expect(await screen.findByText(/Algo Execution/)).toBeInTheDocument();
    expect(screen.getByText(/Off/)).toBeInTheDocument();
  });

  it('hidden when no strategies returned (e.g. Schwab)', async () => {
    mockGetCap.mockResolvedValueOnce({ strategies: [] });
    const { container } = render(
      <AlgoSection
        brokerId="schwab"
        assetClass="STOCK"
        onAlgoChange={vi.fn()}
      />,
    );
    await waitFor(() => expect(container).toBeEmptyDOMElement());
  });

  it('shows LIMIT coercion notice for ICEBERG', async () => {
    mockGetCap.mockResolvedValueOnce({
      strategies: [
        { strategy: 'ICEBERG', params: [{ name: 'display_size', type: 'decimal', required: true }] },
      ],
    });
    const onChange = vi.fn();
    render(<AlgoSection brokerId="ibkr" assetClass="STOCK" onAlgoChange={onChange} />);
    const chip = await screen.findByText(/Algo Execution/);
    fireEvent.click(chip);
    // Select ICEBERG
    const select = screen.getByRole('combobox');
    fireEvent.change(select, { target: { value: 'ICEBERG' } });
    expect(await screen.findByText(/forced to LIMIT/i)).toBeInTheDocument();
  });

  it('shows MARKET coercion notice for TWAP', async () => {
    mockGetCap.mockResolvedValueOnce({
      strategies: [
        {
          strategy: 'TWAP',
          params: [
            { name: 'start_time', type: 'time', required: true },
            { name: 'end_time', type: 'time', required: true },
          ],
        },
      ],
    });
    render(<AlgoSection brokerId="ibkr" assetClass="STOCK" onAlgoChange={vi.fn()} />);
    const chip = await screen.findByText(/Algo Execution/);
    fireEvent.click(chip);
    const select = screen.getByRole('combobox');
    fireEvent.change(select, { target: { value: 'TWAP' } });
    expect(await screen.findByText(/forced to MARKET/i)).toBeInTheDocument();
  });
});
