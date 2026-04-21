import { useEffect, useState } from 'react';
import type { JSX } from 'react';
import { Button } from '@/components/primitives/Button';
import { getHealth } from '@/services/api';

export default function App(): JSX.Element {
  const [status, setStatus] = useState<string>('…');

  useEffect(() => {
    getHealth()
      .then((r) => setStatus(r.status))
      .catch(() => setStatus('unreachable'));
  }, []);

  return (
    <main className="grid min-h-screen place-items-center p-6">
      <div className="text-center">
        <h1 className="text-2xl font-bold">Trading Dashboard</h1>
        <p className="mt-2 text-sm opacity-70">Backend: {status}</p>
        <Button className="mt-4" onClick={() => location.reload()}>
          Recheck
        </Button>
      </div>
    </main>
  );
}
