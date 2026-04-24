import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { ColumnCustomizerDialog } from './ColumnCustomizerDialog';
import type { WatchlistColumnKey } from '@/services/types';

function stubRadixPointer(): void {
  const proto = Element.prototype as unknown as Record<string, unknown>;
  if (typeof proto['hasPointerCapture'] !== 'function') proto['hasPointerCapture'] = () => false;
  if (typeof proto['releasePointerCapture'] !== 'function') proto['releasePointerCapture'] = () => { /* jsdom stub */ };
  if (typeof proto['setPointerCapture'] !== 'function') proto['setPointerCapture'] = () => { /* jsdom stub */ };
  if (typeof proto['scrollIntoView'] !== 'function') proto['scrollIntoView'] = () => { /* jsdom stub */ };
}

describe('ColumnCustomizerDialog', () => {
  beforeEach(() => { stubRadixPointer(); });

  const initial: WatchlistColumnKey[] = ['symbol', 'last', 'change'];

  function renderDialog(onApply = vi.fn()): ReturnType<typeof vi.fn> {
    render(
      <ColumnCustomizerDialog
        open={true}
        onOpenChange={() => { /* noop */ }}
        selected={initial}
        onApply={onApply}
      />,
    );
    return onApply;
  }

  it('renders Available + Selected lists with correct headings', () => {
    renderDialog();
    expect(screen.getByRole('listbox', { name: 'Available' })).toBeInTheDocument();
    expect(screen.getByRole('listbox', { name: 'Selected' })).toBeInTheDocument();
  });

  it('add: picks an Available item and clicks → moves to Selected', async () => {
    const user = userEvent.setup();
    const onApply = renderDialog();

    const available = screen.getByRole('listbox', { name: 'Available' });
    const volumeOpt = screen.getAllByText('Volume').find(el => available.contains(el));
    if (!volumeOpt) throw new Error('Volume not in Available');
    await user.click(volumeOpt);

    await user.click(screen.getByRole('button', { name: /add column/i }));
    await user.click(screen.getByText('Apply'));
    expect(onApply).toHaveBeenCalledWith(['symbol','last','change','volume']);
  });

  it('remove: picks a Selected item and clicks → moves to Available', async () => {
    const user = userEvent.setup();
    const onApply = renderDialog();

    const selected = screen.getByRole('listbox', { name: 'Selected' });
    const lastOpt = screen.getAllByText('Last').find(el => selected.contains(el));
    if (!lastOpt) throw new Error('Last not in Selected');
    await user.click(lastOpt);

    await user.click(screen.getByRole('button', { name: /remove column/i }));
    await user.click(screen.getByText('Apply'));
    expect(onApply).toHaveBeenCalledWith(['symbol','change']);
  });

  it('reorder: select last item, click move up twice → reorder persists on Apply', async () => {
    const user = userEvent.setup();
    const onApply = renderDialog();

    const selected = screen.getByRole('listbox', { name: 'Selected' });
    const changeOpt = screen.getAllByText('Change').find(el => selected.contains(el));
    if (!changeOpt) throw new Error('Change not in Selected');
    await user.click(changeOpt);

    await user.click(screen.getByRole('button', { name: /move up/i }));
    await user.click(screen.getByRole('button', { name: /move up/i }));
    await user.click(screen.getByText('Apply'));
    expect(onApply).toHaveBeenCalledWith(['change','symbol','last']);
  });

  it('apply: fires onApply with the working list; Cancel does not', async () => {
    const user = userEvent.setup();
    const onApply = vi.fn();
    const onOpen = vi.fn();
    render(
      <ColumnCustomizerDialog open={true} onOpenChange={onOpen} selected={initial} onApply={onApply} />,
    );
    await user.click(screen.getByText('Cancel'));
    expect(onApply).not.toHaveBeenCalled();
  });
});
