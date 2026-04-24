import { describe, it, expect } from 'vitest';
import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { RadioGroup, RadioItem } from './Radio';

function renderGroup(props: { disabled?: boolean } = {}) {
  return render(
    <RadioGroup aria-label="size" {...props}>
      <RadioItem value="a" aria-label="a" />
      <RadioItem value="b" aria-label="b" />
      <RadioItem value="c" aria-label="c" />
    </RadioGroup>,
  );
}

describe('Radio', () => {
  it('renders all radio items with radio role', () => {
    renderGroup();
    const radios = screen.getAllByRole('radio');
    expect(radios).toHaveLength(3);
  });

  it('checking one item unchecks the others (uncontrolled)', async () => {
    const user = userEvent.setup();
    renderGroup();
    const [first, second] = screen.getAllByRole('radio');
    if (!first || !second) throw new Error('expected two radio items');
    await user.click(first);
    expect(first.getAttribute('data-state')).toBe('checked');
    expect(second.getAttribute('data-state')).toBe('unchecked');
    await user.click(second);
    expect(first.getAttribute('data-state')).toBe('unchecked');
    expect(second.getAttribute('data-state')).toBe('checked');
  });

  it('does not select when group is disabled', async () => {
    const user = userEvent.setup();
    renderGroup({ disabled: true });
    const [first] = screen.getAllByRole('radio');
    if (!first) throw new Error('expected a radio item');
    await user.click(first);
    expect(first.getAttribute('data-state')).toBe('unchecked');
  });
});
