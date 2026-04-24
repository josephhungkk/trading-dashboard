import type { Meta, StoryObj } from '@storybook/react-vite';
import { useState } from 'react';
import { Button } from '@/components/primitives/Button';
import { ColumnCustomizerDialog } from './ColumnCustomizerDialog';
 
import type { WatchlistColumnKey } from '@/services/types';

const meta = {
  title: 'Patterns/ColumnCustomizerDialog',
  component: ColumnCustomizerDialog,
  tags: ['autodocs'],
} satisfies Meta<typeof ColumnCustomizerDialog>;

export default meta;
type Story = StoryObj<typeof meta>;

function Harness({ initial }: { initial: WatchlistColumnKey[] }): React.JSX.Element {
  const [open, setOpen] = useState(true);
  const [selected, setSelected] = useState<WatchlistColumnKey[]>(initial);
  return (
    <>
      <Button onClick={() => setOpen(true)}>Open</Button>
      <div className="mt-2 text-xs text-fg-muted">Current: {selected.join(', ') || '(none)'}</div>
      <ColumnCustomizerDialog open={open} onOpenChange={setOpen} selected={selected} onApply={setSelected} />
    </>
  );
}

// `args` satisfies the required-props type; the Harness owns the real state.
const stubArgs = {
  open: true,
  onOpenChange: () => { /* noop — Harness owns open */ },
  selected: [] as WatchlistColumnKey[],
  onApply: () => { /* noop — Harness owns onApply */ },
};

export const DefaultOpen: Story = {
  args: stubArgs,
  render: () => <Harness initial={['symbol','last','change','changePct','volume']} />,
};

export const PrePopulated: Story = {
  args: stubArgs,
  render: () => <Harness initial={['symbol','description','last','bid','ask','spread','volume','dayHigh','dayLow']} />,
};

export const EmptySelection: Story = {
  args: stubArgs,
  render: () => <Harness initial={[]} />,
};
