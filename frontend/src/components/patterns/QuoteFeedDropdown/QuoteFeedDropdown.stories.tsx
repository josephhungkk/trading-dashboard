import type { Meta, StoryObj } from '@storybook/react-vite';
import { useEffect } from 'react';
import { QuoteFeedDropdown } from './QuoteFeedDropdown';
import { useQuoteFeedStore } from '@/stores/global/quote-feeds';
import type { QuoteFeedStatus } from '@/services/types';

function Seed({ feeds, children }: { feeds: QuoteFeedStatus[]; children: React.ReactNode }): React.JSX.Element {
  useEffect(() => {
    useQuoteFeedStore.setState({ feeds });
  }, [feeds]);
  return <>{children}</>;
}

const allRealtime: QuoteFeedStatus[] = [
  { assetClass: 'stock', exchange: 'NYSE',   feedType: 'realtime' },
  { assetClass: 'stock', exchange: 'NASDAQ', feedType: 'realtime' },
  { assetClass: 'forex',                     feedType: 'realtime' },
];

const someDelayed: QuoteFeedStatus[] = [
  { assetClass: 'stock', exchange: 'NYSE',   feedType: 'realtime' },
  { assetClass: 'stock', exchange: 'NASDAQ', feedType: 'realtime' },
  { assetClass: 'options',                   feedType: 'delayed' },
];

const withLevel2: QuoteFeedStatus[] = [
  { assetClass: 'stock', exchange: 'NYSE', feedType: 'realtime' },
  { assetClass: 'stock', exchange: 'NYSE', feedType: 'delayed', level: 2 },
];

const meta = {
  title: 'Patterns/QuoteFeedDropdown',
  component: QuoteFeedDropdown,
  tags: ['autodocs'],
} satisfies Meta<typeof QuoteFeedDropdown>;

export default meta;
type Story = StoryObj<typeof meta>;

export const AllRealtime: Story = { render: () => <Seed feeds={allRealtime}><QuoteFeedDropdown /></Seed> };
export const SomeDelayed: Story = { render: () => <Seed feeds={someDelayed}><QuoteFeedDropdown /></Seed> };
export const WithLevel2:  Story = { render: () => <Seed feeds={withLevel2}><QuoteFeedDropdown /></Seed> };
