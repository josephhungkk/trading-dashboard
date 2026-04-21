export default {
  extends: ['stylelint-config-standard', 'stylelint-config-clean-order'],
  ignoreFiles: ['src/stories/**/*.css'],
  overrides: [
    {
      files: ['**/*.tsx'],
      customSyntax: 'postcss-html',
    },
  ],
  rules: {
    'unit-disallowed-list': ['px', 'em'],
    'declaration-property-unit-allowed-list': {
      '/^(width|height|min-.+|max-.+|margin.*|padding.*|gap|top|right|bottom|left|font-size|line-height|border-radius|inset.*)$/':
        ['rem', '%', 'vh', 'vw', 'fr', 'auto'],
    },
    'at-rule-no-unknown': [true, { ignoreAtRules: ['theme', 'tailwind', 'apply', 'layer', 'config'] }],
    'custom-property-pattern': null,
    'import-notation': null,
    'hue-degree-notation': null,
    'custom-property-empty-line-before': null,
  },
};
