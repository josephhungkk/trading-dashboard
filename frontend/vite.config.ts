import path from 'node:path';
import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';
import { TanStackRouterVite } from '@tanstack/router-plugin/vite';

export default defineConfig({
  plugins: [
    TanStackRouterVite({
      routesDirectory: './src/routes',
      generatedRouteTree: './src/routes/routeTree.gen.ts',
    }),
    react(),
  ],
  resolve: {
    alias: { '@': path.resolve(__dirname, 'src') },
  },
  server: {
    host: '0.0.0.0',
    port: 5173,
    proxy: {
      '/api':    { target: 'http://10.10.0.2:8000', changeOrigin: true },
      '/health': { target: 'http://10.10.0.2:8000', changeOrigin: true },
    },
  },
});
