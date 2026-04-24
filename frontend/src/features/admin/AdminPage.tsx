import * as React from 'react';
import { Outlet, useNavigate, useRouterState } from '@tanstack/react-router';
import { Tabs, TabsList, TabsTrigger } from '@/components/primitives/Tabs';

type AdminTab = 'config' | 'secrets';

export function AdminPage(): React.JSX.Element {
  const navigate = useNavigate();
  const pathname = useRouterState({ select: (s) => s.location.pathname });
  const active: AdminTab = pathname.endsWith('/secrets') ? 'secrets' : 'config';

  React.useEffect(() => {
    if (pathname === '/admin') void navigate({ to: '/admin/config', replace: true });
  }, [navigate, pathname]);

  function onTabChange(value: string): void {
    const tab = value as AdminTab;
    void navigate({ to: tab === 'secrets' ? '/admin/secrets' : '/admin/config' });
  }

  return (
    <section className="flex h-full min-h-0 flex-col gap-3 p-4" aria-label="Admin">
      <header className="flex items-baseline justify-between">
        <h2 className="text-lg font-semibold text-fg">Admin</h2>
      </header>

      <Tabs value={active} onValueChange={onTabChange}>
        <TabsList>
          <TabsTrigger value="config">Config</TabsTrigger>
          <TabsTrigger value="secrets">Secrets</TabsTrigger>
        </TabsList>
      </Tabs>

      <Outlet />
    </section>
  );
}
