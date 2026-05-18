import { createFileRoute } from '@tanstack/react-router';
import { CryptoPage } from '@/features/crypto/CryptoPage';
export const Route = createFileRoute('/crypto')({ component: CryptoPage });
