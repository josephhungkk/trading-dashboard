import js from '@eslint/js';
import tsEslint from 'typescript-eslint';
import react from 'eslint-plugin-react';
import reactHooks from 'eslint-plugin-react-hooks';
import jsxA11y from 'eslint-plugin-jsx-a11y';
import boundaries from 'eslint-plugin-boundaries';
import globals from 'globals';

export default tsEslint.config(
  { ignores: ['dist', 'storybook-static', 'coverage', 'node_modules', '**/*.d.ts', 'src/stories/**'] },
  js.configs.recommended,
  ...tsEslint.configs.strict,
  ...tsEslint.configs.stylistic,
  {
    languageOptions: {
      globals: { ...globals.browser },
      parserOptions: { ecmaFeatures: { jsx: true } },
    },
    plugins: {
      react,
      'react-hooks': reactHooks,
      'jsx-a11y': jsxA11y,
      boundaries,
    },
    settings: {
      react: { version: 'detect' },
      'import/resolver': {
        typescript: { project: './tsconfig.json' },
        node: true,
      },
      'boundaries/elements': [
        { type: 'tokens',     pattern: 'src/design-tokens/**' },
        { type: 'primitives', pattern: 'src/components/primitives/**' },
        { type: 'patterns',   pattern: 'src/components/patterns/**' },
        { type: 'layout',     pattern: 'src/components/layout/**' },
        { type: 'features',   pattern: 'src/features/**' },
        { type: 'services',   pattern: 'src/services/**' },
        { type: 'factory',    pattern: 'src/stores/factory.ts', mode: 'file' },
        { type: 'scoped',     pattern: 'src/stores/scoped/**' },
        { type: 'stores',     pattern: 'src/stores/**' },
        { type: 'hooks',      pattern: 'src/hooks/**' },
        { type: 'lib',        pattern: 'src/lib/**' },
        { type: 'app',        pattern: 'src/{App,main,vite-env}*' },
      ],
    },
    rules: {
      ...react.configs.recommended.rules,
      ...reactHooks.configs.recommended.rules,
      ...jsxA11y.configs.recommended.rules,
      'react/react-in-jsx-scope': 'off',
      'boundaries/element-types': [
        'error',
        {
          default: 'disallow',
          rules: [
            { from: 'tokens',     allow: [] },
            { from: 'primitives', allow: ['tokens', 'lib'] },
            { from: 'patterns',   allow: ['tokens', 'primitives', 'patterns', 'lib'] },
            { from: 'layout',     allow: ['tokens', 'primitives', 'patterns', 'layout', 'lib'] },
            { from: 'features',   allow: ['tokens', 'primitives', 'patterns', 'layout', 'features', 'services', 'stores', 'hooks', 'lib'] },
            { from: 'services',   allow: ['lib'] },
            { from: 'stores',     allow: ['services', 'lib', 'factory'] },
            { from: 'scoped',     allow: ['services', 'lib'] },
            { from: 'factory',    allow: ['scoped', 'services', 'lib'] },
            { from: 'hooks',      allow: ['services', 'stores', 'lib'] },
            { from: 'lib',        allow: ['lib'] },
            { from: 'app',        allow: ['tokens', 'primitives', 'patterns', 'layout', 'features', 'services', 'stores', 'hooks', 'lib'] },
          ],
        },
      ],
      'no-restricted-imports': ['error', {
        patterns: [{
          group: ['@/stores/scoped/*', '**/stores/scoped/*'],
          message: 'Do not import scoped stores directly. Use useActiveStores() from @/stores/registry.',
        }],
      }],
    },
  },
  {
    files: ['**/*.stories.tsx', '**/*.test.tsx'],
    rules: { 'boundaries/element-types': 'off' },
  },
  {
    files: ['src/stores/factory.ts', 'src/stores/registry.ts', 'src/stores/scoped/**'],
    rules: { 'no-restricted-imports': 'off' },
  },
);
