import { describe, it, expect } from 'vitest';
import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { Tabs, TabsList, TabsTrigger, TabsContent } from './Tabs';

// Radix Tabs relies on pointer-capture methods jsdom does not implement.
// Stub just enough to let userEvent drive the component.
function stubRadixPointer(): void {
  const proto = Element.prototype as unknown as Record<string, unknown>;
  if (typeof proto['hasPointerCapture'] !== 'function') {
    proto['hasPointerCapture'] = () => false;
  }
  if (typeof proto['releasePointerCapture'] !== 'function') {
    proto['releasePointerCapture'] = () => { /* jsdom stub */ };
  }
  if (typeof proto['setPointerCapture'] !== 'function') {
    proto['setPointerCapture'] = () => { /* jsdom stub */ };
  }
  if (typeof proto['scrollIntoView'] !== 'function') {
    proto['scrollIntoView'] = () => { /* jsdom stub */ };
  }
}

function renderBasic(props: { ordersDisabled?: boolean; defaultValue?: string } = {}) {
  const { defaultValue = 'positions', ordersDisabled = false } = props;
  return render(
    <Tabs defaultValue={defaultValue}>
      <TabsList>
        <TabsTrigger value="positions">Positions</TabsTrigger>
        <TabsTrigger value="orders" disabled={ordersDisabled}>
          Orders
        </TabsTrigger>
        <TabsTrigger value="activity">Activity</TabsTrigger>
      </TabsList>
      <TabsContent value="positions">Positions panel</TabsContent>
      <TabsContent value="orders">Orders panel</TabsContent>
      <TabsContent value="activity">Activity panel</TabsContent>
    </Tabs>,
  );
}

describe('Tabs', () => {
  it('renders the default selected tab content on mount', () => {
    stubRadixPointer();
    renderBasic({ defaultValue: 'orders' });
    expect(screen.getByText('Orders panel')).toBeInTheDocument();
    expect(screen.queryByText('Positions panel')).not.toBeInTheDocument();
    expect(screen.queryByText('Activity panel')).not.toBeInTheDocument();
  });

  it('switches content when a different tab is clicked', async () => {
    stubRadixPointer();
    const user = userEvent.setup();
    renderBasic();
    expect(screen.getByText('Positions panel')).toBeInTheDocument();
    await user.click(screen.getByRole('tab', { name: 'Activity' }));
    expect(screen.getByText('Activity panel')).toBeInTheDocument();
    expect(screen.queryByText('Positions panel')).not.toBeInTheDocument();
  });

  it('does not activate a disabled tab when clicked', async () => {
    stubRadixPointer();
    const user = userEvent.setup();
    renderBasic({ ordersDisabled: true });
    const ordersTab = screen.getByRole('tab', { name: 'Orders' });
    expect(ordersTab.getAttribute('data-state')).toBe('inactive');
    await user.click(ordersTab);
    expect(ordersTab.getAttribute('data-state')).toBe('inactive');
    expect(screen.getByText('Positions panel')).toBeInTheDocument();
    expect(screen.queryByText('Orders panel')).not.toBeInTheDocument();
  });
});
