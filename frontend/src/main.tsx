import { StrictMode } from 'react';
import { createRoot } from 'react-dom/client';
import App from './App';
import { registerCustomIndicators } from './features/chart/indicators';
import './styles/global.css';

const rootElement = document.getElementById('root');
if (!rootElement) throw new Error('root element not found');

registerCustomIndicators();

createRoot(rootElement).render(
  <StrictMode>
    <App />
  </StrictMode>,
);
