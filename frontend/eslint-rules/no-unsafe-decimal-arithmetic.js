/**
 * Disallow `Number(x.value)` patterns on Money-shaped objects.
 *
 * The backend serializes Money/Decimal as fixed-precision strings so values
 * survive the wire intact (e.g. "100.00", "0.10000000"). `Number(money.value)`
 * silently truncates that ("100.00" -> 100, "0.10000000" -> 0.1) and any
 * arithmetic on the result is lossy.
 *
 * Use `safeParseDecimal(money.value)` from `@/lib/decimal` instead -- the
 * `lossy` flag on the returned record makes the precision loss explicit,
 * `display` gives you the rounded number when you genuinely need one (chart
 * axes etc.), and `precise` returns the original string for comparisons.
 */
const PROPERTY_NAMES = new Set(['value', 'precise']);

/** @type {import('eslint').Rule.RuleModule} */
const rule = {
  meta: {
    type: 'problem',
    docs: {
      description:
        'Disallow Number(x.value) on Money-shaped objects; use safeParseDecimal() instead.',
      recommended: false,
    },
    schema: [],
    messages: {
      unsafeNumberCoercion:
        'Avoid Number(x.{{property}}) on decimal-string fields. Use safeParseDecimal(x.{{property}}).display from @/lib/decimal so the lossy flag is surfaced.',
    },
  },
  create(context) {
    return {
      CallExpression(node) {
        if (
          node.callee.type === 'Identifier' &&
          node.callee.name === 'Number' &&
          node.arguments.length === 1
        ) {
          const arg = node.arguments[0];
          if (
            arg.type === 'MemberExpression' &&
            arg.property.type === 'Identifier' &&
            PROPERTY_NAMES.has(arg.property.name)
          ) {
            context.report({
              node,
              messageId: 'unsafeNumberCoercion',
              data: { property: arg.property.name },
            });
          }
        }
      },
    };
  },
};

export default rule;
