import type { Meta, StoryObj } from '@storybook/react-vite';
import {
  RouterProvider,
  createRootRoute,
  createRoute,
  createRouter,
  createMemoryHistory,
  Outlet,
} from '@tanstack/react-router';
import { ViewChartLink } from './ViewChartLink';

// Minimal router wrapper so Link can resolve routes in Storybook.
function withRouter(ui: React.ReactNode): React.ReactElement {
  const rootRoute = createRootRoute({ component: () => <Outlet /> });
  const indexRoute = createRoute({
    getParentRoute: () => rootRoute,
    path: '/',
    component: () => <>{ui}</>,
  });
  const chartRoute = createRoute({
    getParentRoute: () => rootRoute,
    path: '/chart/$canonicalId',
    component: () => <div />,
  });
  const routeTree = rootRoute.addChildren([indexRoute, chartRoute]);
  const router = createRouter({
    routeTree,
    history: createMemoryHistory({ initialEntries: ['/'] }),
  });
  return <RouterProvider router={router as never} />;
}

const meta = {
  title: 'Primitives/ViewChartLink',
  component: ViewChartLink,
  tags: ['autodocs'],
  decorators: [
    (Story) => withRouter(<Story />),
  ],
  argTypes: {
    canonicalId: { control: 'text' },
  },
} satisfies Meta<typeof ViewChartLink>;

export default meta;
type Story = StoryObj<typeof meta>;

export const WithCanonicalId: Story = {
  args: { canonicalId: 'AAPL.US' },
};

export const NullCanonicalId: Story = {
  args: { canonicalId: null },
};

export const UndefinedCanonicalId: Story = {
  args: { canonicalId: undefined },
};
