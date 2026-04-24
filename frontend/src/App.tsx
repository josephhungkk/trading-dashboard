import { RouterProvider, createRouter } from '@tanstack/react-router';
import type { JSX } from 'react';
import { routeTree } from './routes/routeTree.gen';

const router = createRouter({ routeTree });

declare module '@tanstack/react-router' {
  interface Register { router: typeof router; }
}

export default function App(): JSX.Element {
  return <RouterProvider router={router} />;
}
