import type { Preview } from '@storybook/react-vite';
import '../src/styles/global.css';

if (typeof import.meta.env !== 'undefined') {
  (import.meta.env as Record<string, string>).VITE_USE_MOCKS = 'true';
}

const preview: Preview = {
  parameters: {
    controls: {
      matchers: {
        color: /(background|color)$/i,
        date: /Date$/i,
      },
    },
    viewport: {
      viewports: {
        mobile: { name: 'Mobile', styles: { width: '375px', height: '667px' } },
        tablet: { name: 'Tablet', styles: { width: '768px', height: '1024px' } },
        desktop: { name: 'Desktop', styles: { width: '1440px', height: '900px' } },
      },
    },
    backgrounds: {
      default: 'light',
      values: [
        { name: 'light', value: 'hsl(0 0% 100%)' },
        { name: 'dark', value: 'hsl(222 15% 12%)' },
      ],
    },
    a11y: { test: 'todo' },
  },
};

export default preview;