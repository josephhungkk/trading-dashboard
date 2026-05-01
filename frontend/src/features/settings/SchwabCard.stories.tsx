import type { Meta, StoryObj } from '@storybook/react-vite';
import { SchwabCard } from './SchwabCard';
import * as schwabHook from '@/hooks/useSchwabTokenStatus';

type SchwabHookReturn = ReturnType<typeof schwabHook.useSchwabTokenStatus>;
type SchwabStatus = SchwabHookReturn['status'];

const HOUR_MS = 3_600_000;

function makeStatus({
  refreshTokenAgeHours,
  tier2RefreshEnabled,
  tier2ConsecutiveFailures,
}: {
  refreshTokenAgeHours: number;
  tier2RefreshEnabled: boolean;
  tier2ConsecutiveFailures: number;
}): NonNullable<SchwabStatus> {
  const now = Date.now();
  return {
    accessTokenIssuedAt: new Date(),
    refreshTokenIssuedAt: new Date(now - refreshTokenAgeHours * HOUR_MS),
    tier2RefreshEnabled,
    tier2ConsecutiveFailures,
  };
}

function makeHookReturn(status: SchwabStatus): SchwabHookReturn {
  return {
    status,
    loading: false,
    error: null,
    refetch: () => Promise.resolve(),
    startFastPoll: () => undefined,
  };
}

function withSchwabStatus(status: SchwabStatus) {
  return function SchwabStatusDecorator(Story: () => React.JSX.Element): React.JSX.Element {
    Object.defineProperty(schwabHook, 'useSchwabTokenStatus', {
      configurable: true,
      value: () => makeHookReturn(status),
    });
    return <Story />;
  };
}

const meta = {
  title: 'Features/SchwabCard',
  component: SchwabCard,
  tags: ['autodocs'],
  parameters: { layout: 'centered' },
  decorators: [
    (Story) => (
      <div className="w-96">
        <Story />
      </div>
    ),
  ],
} satisfies Meta<typeof SchwabCard>;

export default meta;
type Story = StoryObj<typeof meta>;

export const Disconnected: Story = {
  decorators: [withSchwabStatus(null)],
  render: () => <SchwabCard />,
};

export const ConnectedFresh: Story = {
  decorators: [
    withSchwabStatus(
      makeStatus({
        refreshTokenAgeHours: 24,
        tier2RefreshEnabled: false,
        tier2ConsecutiveFailures: 0,
      }),
    ),
  ],
  render: () => <SchwabCard />,
};

export const ExpiringSoon: Story = {
  decorators: [
    withSchwabStatus(
      makeStatus({
        refreshTokenAgeHours: 145,
        tier2RefreshEnabled: false,
        tier2ConsecutiveFailures: 0,
      }),
    ),
  ],
  render: () => <SchwabCard />,
};

export const Expired: Story = {
  decorators: [
    withSchwabStatus(
      makeStatus({
        refreshTokenAgeHours: 170,
        tier2RefreshEnabled: false,
        tier2ConsecutiveFailures: 0,
      }),
    ),
  ],
  render: () => <SchwabCard />,
};

export const Tier2EnabledNoFailures: Story = {
  decorators: [
    withSchwabStatus(
      makeStatus({
        refreshTokenAgeHours: 24,
        tier2RefreshEnabled: true,
        tier2ConsecutiveFailures: 0,
      }),
    ),
  ],
  render: () => <SchwabCard />,
};

export const Tier2WithFailures: Story = {
  decorators: [
    withSchwabStatus(
      makeStatus({
        refreshTokenAgeHours: 24,
        tier2RefreshEnabled: true,
        tier2ConsecutiveFailures: 2,
      }),
    ),
  ],
  render: () => <SchwabCard />,
};
