import type { Meta, StoryObj } from '@storybook/react-vite';
import {
  Select,
  SelectTrigger,
  SelectContent,
  SelectItem,
  SelectValue,
  SelectGroup,
  SelectLabel,
} from './Select';

const meta = {
  title: 'Primitives/Select',
  component: Select,
  tags: ['autodocs'],
} satisfies Meta<typeof Select>;

export default meta;
type Story = StoryObj<typeof meta>;

export const Single: Story = {
  render: () => (
    <div className="w-64">
      <Select>
        <SelectTrigger aria-label="broker">
          <SelectValue placeholder="Select a broker" />
        </SelectTrigger>
        <SelectContent>
          <SelectItem value="ibkr">Interactive Brokers</SelectItem>
          <SelectItem value="futu">Futu Securities</SelectItem>
          <SelectItem value="schwab">Charles Schwab</SelectItem>
        </SelectContent>
      </Select>
    </div>
  ),
};

export const Grouped: Story = {
  render: () => (
    <div className="w-64">
      <Select>
        <SelectTrigger aria-label="asset class">
          <SelectValue placeholder="Select an asset class" />
        </SelectTrigger>
        <SelectContent>
          <SelectGroup>
            <SelectLabel>Equities</SelectLabel>
            <SelectItem value="stocks">Stocks</SelectItem>
            <SelectItem value="etf">ETFs</SelectItem>
          </SelectGroup>
          <SelectGroup>
            <SelectLabel>Derivatives</SelectLabel>
            <SelectItem value="options">Options</SelectItem>
            <SelectItem value="futures">Futures</SelectItem>
          </SelectGroup>
        </SelectContent>
      </Select>
    </div>
  ),
};

export const WithDefaultValue: Story = {
  render: () => (
    <div className="w-64">
      <Select defaultValue="usd">
        <SelectTrigger aria-label="currency">
          <SelectValue />
        </SelectTrigger>
        <SelectContent>
          <SelectItem value="usd">USD</SelectItem>
          <SelectItem value="hkd">HKD</SelectItem>
          <SelectItem value="gbp">GBP</SelectItem>
          <SelectItem value="jpy">JPY</SelectItem>
        </SelectContent>
      </Select>
    </div>
  ),
};

export const Disabled: Story = {
  render: () => (
    <div className="w-64">
      <Select disabled defaultValue="ibkr">
        <SelectTrigger aria-label="broker">
          <SelectValue />
        </SelectTrigger>
        <SelectContent>
          <SelectItem value="ibkr">Interactive Brokers</SelectItem>
          <SelectItem value="futu">Futu Securities</SelectItem>
        </SelectContent>
      </Select>
    </div>
  ),
};
