import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { act, fireEvent, render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { ContractSearchInput } from './ContractSearchInput';
import { createDebouncedSearch } from '../../services/orders';
import type { ContractSummary } from '../../services/types';

type DisplayContract = ContractSummary & {
  symbol: string;
  exchange: string;
  asset_class: string;
};

const searchMock = vi.hoisted(() => vi.fn<(
  q: string,
  assetClass?: string,
) => Promise<ContractSummary[]>>());

vi.mock('../../services/orders', () => ({
  createDebouncedSearch: vi.fn(() => searchMock),
}));

const contracts: DisplayContract[] = [
  { conid: 265598, description: 'AAPL NASDAQ', symbol: 'AAPL', exchange: 'NASDAQ', asset_class: 'stock' },
  { conid: 8314, description: 'MSFT NASDAQ', symbol: 'MSFT', exchange: 'NASDAQ', asset_class: 'stock' },
];

async function flushSearch(): Promise<void> {
  await act(async () => {
    await vi.advanceTimersByTimeAsync(300);
  });
}

describe('ContractSearchInput', () => {
  beforeEach(() => {
    vi.useFakeTimers();
    searchMock.mockReset();
    searchMock.mockResolvedValue(contracts);
    vi.mocked(createDebouncedSearch).mockClear();
  });

  afterEach(() => {
    vi.useRealTimers();
  });

  it('renders_combobox_role', () => {
    render(<ContractSearchInput onSelect={() => undefined} />);

    expect(screen.getByRole('combobox')).toBeInTheDocument();
  });

  it('aria_attributes_correctly_wired', async () => {
    render(<ContractSearchInput onSelect={() => undefined} />);
    const input = screen.getByRole('combobox');

    expect(input).toHaveAttribute('aria-expanded', 'false');
    fireEvent.change(input, { target: { value: 'AAP' } });
    expect(input).toHaveAttribute('aria-expanded', 'true');

    await flushSearch();
    expect(screen.getByRole('option', { name: 'AAPL · NASDAQ · stock' })).toBeInTheDocument();

    fireEvent.keyDown(input, { key: 'ArrowDown' });
    const firstOption = screen.getByRole('option', { name: 'AAPL · NASDAQ · stock' });
    expect(input).toHaveAttribute('aria-activedescendant', firstOption.id);
  });

  it('debounces_300ms', async () => {
    render(<ContractSearchInput onSelect={() => undefined} />);
    const input = screen.getByRole('combobox');

    fireEvent.change(input, { target: { value: 'A' } });
    fireEvent.change(input, { target: { value: 'AA' } });
    fireEvent.change(input, { target: { value: 'AAP' } });
    fireEvent.change(input, { target: { value: 'AAPL' } });
    fireEvent.change(input, { target: { value: 'AAPL ' } });
    fireEvent.change(input, { target: { value: 'AAPL N' } });

    await act(async () => {
      await vi.advanceTimersByTimeAsync(299);
    });
    expect(searchMock).not.toHaveBeenCalled();

    await act(async () => {
      await vi.advanceTimersByTimeAsync(1);
    });
    expect(searchMock).toHaveBeenCalledTimes(1);
    expect(searchMock).toHaveBeenCalledWith('AAPL N', undefined);
  });

  it('aborts_in_flight_on_new_keystroke', async () => {
    const signals: AbortSignal[] = [];
    const OriginalAbortController = globalThis.AbortController;

    class CapturingAbortController extends OriginalAbortController {
      constructor() {
        super();
        signals.push(this.signal);
      }
    }

    vi.stubGlobal('AbortController', CapturingAbortController);
    searchMock.mockReturnValue(new Promise<ContractSummary[]>(() => undefined));
    render(<ContractSearchInput onSelect={() => undefined} />);
    const input = screen.getByRole('combobox');

    fireEvent.change(input, { target: { value: 'AAPL' } });
    await flushSearch();
    fireEvent.change(input, { target: { value: 'MSFT' } });

    expect(signals[0]?.aborted).toBe(true);
    vi.unstubAllGlobals();
  });

  it('selecting_option_calls_onSelect_with_conid_and_symbol', async () => {
    const onSelect = vi.fn();
    render(<ContractSearchInput onSelect={onSelect} />);

    fireEvent.change(screen.getByRole('combobox'), { target: { value: 'MS' } });
    await flushSearch();

    vi.useRealTimers();
    const user = userEvent.setup();
    await user.click(screen.getByRole('option', { name: 'MSFT · NASDAQ · stock' }));

    expect(onSelect).toHaveBeenCalledWith({ conid: '8314', symbol: 'MSFT' });
  });

  it('empty_state_shows_no_matches_when_results_empty', async () => {
    searchMock.mockResolvedValue([]);
    render(<ContractSearchInput onSelect={() => undefined} />);

    fireEvent.change(screen.getByRole('combobox'), { target: { value: 'ZZZ' } });
    await flushSearch();

    expect(screen.getByText('No matches')).toBeInTheDocument();
  });
});
