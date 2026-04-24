import { createFileRoute } from '@tanstack/react-router';
import { OrdersPage } from '@/features/orders/OrdersPage';

export const Route = createFileRoute('/orders')({ component: OrdersPage });
