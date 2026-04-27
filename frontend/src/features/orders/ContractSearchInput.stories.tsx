import type { Meta, StoryObj } from '@storybook/react-vite';
import { useEffect } from 'react';
import { within, userEvent, expect } from 'storybook/test';
import { ContractSearchInput } from './ContractSearchInput';
import type { ContractSummary } from '../../services/types';

type DisplayContract = ContractSummary & {
  symbol: string;
  exchange: string;
  asset_class: string;
};

type SearchFn = (q: string, assetClass?: string) => Promise<ContractSummary[]>;

interface StoryProps {
  onSelect: (contract: { conid: string; symbol: string }) => void;
  assetClass?: string;
  disabled?: boolean;
}

const sampleContracts: DisplayContract[] = [
  { conid: 265598, description: 'AAPL NASDAQ', symbol: 'AAPL', exchange: 'NASDAQ', asset_class: 'stock' },
  { conid: 8314, description: 'MSFT NASDAQ', symbol: 'MSFT', exchange: 'NASDAQ', asset_class: 'stock' },
  { conid: 76792991, description: 'TSLA NASDAQ', symbol: 'TSLA', exchange: 'NASDAQ', asset_class: 'stock' },
];

function sleep(ms: number): Promise<void> {
  return new Promise((resolve) => {
    window.setTimeout(resolve, ms);
  });
}

function SearchHarness({
  search,
}: {
  search: SearchFn;
}): React.JSX.Element {
  useEffect(() => {
    window.__contractSearchInputSearchFactory = () => search;
    return () => {
      delete window.__contractSearchInputSearchFactory;
    };
  }, [search]);

  return (
    <div className="w-[24rem]">
      <ContractSearchInput onSelect={() => undefined} assetClass="stock" />
    </div>
  );
}

function StoryComponent(props: StoryProps): React.JSX.Element {
  return <ContractSearchInput {...props} />;
}

const meta = {
  title: 'Features/ContractSearchInput',
  component: StoryComponent,
  tags: ['autodocs'],
  args: {
    onSelect: () => undefined,
  },
  decorators: [
    (Story) => (
      <div className="p-6">
        <Story />
      </div>
    ),
  ],
} satisfies Meta<typeof StoryComponent>;

export default meta;
type Story = StoryObj<typeof meta>;

export const Empty: Story = {
  args: {
    onSelect: () => undefined,
  },
  render: () => (
    <SearchHarness search={() => Promise.resolve([])} />
  ),
};

export const LoadingResults: Story = {
  args: {
    onSelect: () => undefined,
  },
  render: () => (
    <SearchHarness search={() => sleep(5000).then(() => sampleContracts)} />
  ),
  play: async ({ canvasElement }) => {
    const canvas = within(canvasElement);
    await userEvent.type(canvas.getByRole('combobox'), 'AAP');
    await expect(await canvas.findByText('Searching...')).toBeInTheDocument();
  },
};

export const WithResults: Story = {
  args: {
    onSelect: () => undefined,
  },
  render: () => (
    <SearchHarness search={() => Promise.resolve(sampleContracts)} />
  ),
  play: async ({ canvasElement }) => {
    const canvas = within(canvasElement);
    await userEvent.type(canvas.getByRole('combobox'), 'AAPL');
    await expect(await canvas.findByRole('option', { name: 'AAPL · NASDAQ · stock' })).toBeInTheDocument();
  },
};

export const NoMatches: Story = {
  args: {
    onSelect: () => undefined,
  },
  render: () => (
    <SearchHarness search={() => Promise.resolve([])} />
  ),
  play: async ({ canvasElement }) => {
    const canvas = within(canvasElement);
    await userEvent.type(canvas.getByRole('combobox'), 'ZZZ');
    await expect(await canvas.findByText('No matches')).toBeInTheDocument();
  },
};

export const RateLimited: Story = {
  args: {
    onSelect: () => undefined,
  },
  render: () => (
    <SearchHarness search={() => Promise.reject(new Error('rate limited'))} />
  ),
  play: async ({ canvasElement }) => {
    const canvas = within(canvasElement);
    await userEvent.type(canvas.getByRole('combobox'), 'AAPL');
    await expect(await canvas.findByText('Search failed; retry')).toBeInTheDocument();
  },
};
