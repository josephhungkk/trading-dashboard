import type { Meta, StoryObj } from '@storybook/react-vite';
import { Tabs, TabsList, TabsTrigger, TabsContent } from './Tabs';

const meta = {
  title: 'Primitives/Tabs',
  component: Tabs,
  tags: ['autodocs'],
} satisfies Meta<typeof Tabs>;

export default meta;
type Story = StoryObj<typeof meta>;

export const Basic: Story = {
  render: () => (
    <Tabs defaultValue="positions" className="w-96">
      <TabsList>
        <TabsTrigger value="positions">Positions</TabsTrigger>
        <TabsTrigger value="orders">Orders</TabsTrigger>
        <TabsTrigger value="activity">Activity</TabsTrigger>
      </TabsList>
      <TabsContent value="positions">
        <p className="text-sm text-fg">Open positions across all accounts.</p>
      </TabsContent>
      <TabsContent value="orders">
        <p className="text-sm text-fg">Pending and filled orders today.</p>
      </TabsContent>
      <TabsContent value="activity">
        <p className="text-sm text-fg">Recent trade activity and cash flow.</p>
      </TabsContent>
    </Tabs>
  ),
};

export const WithDefault: Story = {
  render: () => (
    <Tabs defaultValue="orders" className="w-96">
      <TabsList>
        <TabsTrigger value="positions">Positions</TabsTrigger>
        <TabsTrigger value="orders">Orders</TabsTrigger>
        <TabsTrigger value="activity">Activity</TabsTrigger>
      </TabsList>
      <TabsContent value="positions">
        <p className="text-sm text-fg">Open positions.</p>
      </TabsContent>
      <TabsContent value="orders">
        <p className="text-sm text-fg">Orders tab is selected by default.</p>
      </TabsContent>
      <TabsContent value="activity">
        <p className="text-sm text-fg">Recent activity.</p>
      </TabsContent>
    </Tabs>
  ),
};

export const Disabled: Story = {
  render: () => (
    <Tabs defaultValue="positions" className="w-96">
      <TabsList>
        <TabsTrigger value="positions">Positions</TabsTrigger>
        <TabsTrigger value="orders" disabled>
          Orders
        </TabsTrigger>
        <TabsTrigger value="activity">Activity</TabsTrigger>
      </TabsList>
      <TabsContent value="positions">
        <p className="text-sm text-fg">Open positions.</p>
      </TabsContent>
      <TabsContent value="orders">
        <p className="text-sm text-fg">This tab is disabled.</p>
      </TabsContent>
      <TabsContent value="activity">
        <p className="text-sm text-fg">Recent activity.</p>
      </TabsContent>
    </Tabs>
  ),
};
