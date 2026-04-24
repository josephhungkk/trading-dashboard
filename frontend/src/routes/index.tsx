import { createFileRoute, redirect } from '@tanstack/react-router';

export const Route = createFileRoute('/')({
  // @ts-expect-error — `/overview` lands in Task 6; suppression self-cleans once route exists
  beforeLoad: () => { throw redirect({ to: '/overview' }); },
});
