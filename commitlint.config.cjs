// commitlint resolves `extends` relative to this file's directory by default,
// but config-conventional is installed under frontend/node_modules. Resolve it
// explicitly so the hook works regardless of which CWD commitlint runs from.
const path = require('path');
module.exports = {
  extends: [
    require.resolve('@commitlint/config-conventional', {
      paths: [path.join(__dirname, 'frontend')],
    }),
  ],
  rules: {
    'type-enum': [
      2,
      'always',
      ['feat', 'fix', 'refactor', 'docs', 'test', 'chore', 'perf', 'ci'],
    ],
  },
};
