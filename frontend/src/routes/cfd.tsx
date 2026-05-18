import { createFileRoute } from '@tanstack/react-router';

import { CFDPage } from '@/features/cfd/CFDPage';

export const Route = createFileRoute('/cfd')({
  component: CFDPage,
});
