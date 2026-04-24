import { describe, it, expect } from 'vitest';
import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { Popover, PopoverTrigger, PopoverContent } from './Popover';

// Radix overlay primitives rely on PointerEvents and hasPointerCapture which
// jsdom does not implement. Stub just enough to let userEvent drive them.
function stubRadixPointer(): void {
  const proto = Element.prototype as unknown as Record<string, unknown>;
  if (typeof proto['hasPointerCapture'] !== 'function') {
    proto['hasPointerCapture'] = () => false;
  }
  if (typeof proto['releasePointerCapture'] !== 'function') {
    proto['releasePointerCapture'] = () => { /* jsdom stub */ };
  }
  if (typeof proto['setPointerCapture'] !== 'function') {
    proto['setPointerCapture'] = () => { /* jsdom stub */ };
  }
  if (typeof proto['scrollIntoView'] !== 'function') {
    proto['scrollIntoView'] = () => { /* jsdom stub */ };
  }
}

function renderBasic() {
  return render(
    <Popover>
      <PopoverTrigger>Open</PopoverTrigger>
      <PopoverContent>
        <span data-testid="popover-body">Popover body</span>
      </PopoverContent>
    </Popover>,
  );
}

describe('Popover', () => {
  it('renders the trigger without rendering the content initially', () => {
    stubRadixPointer();
    renderBasic();
    expect(screen.getByRole('button', { name: 'Open' })).toBeInTheDocument();
    expect(screen.queryByTestId('popover-body')).not.toBeInTheDocument();
  });

  it('opens the popover when the trigger is clicked', async () => {
    stubRadixPointer();
    const user = userEvent.setup();
    renderBasic();
    await user.click(screen.getByRole('button', { name: 'Open' }));
    expect(screen.getByTestId('popover-body')).toBeInTheDocument();
    expect(screen.getByRole('dialog')).toBeInTheDocument();
  });

  it('closes the popover when Escape is pressed', async () => {
    stubRadixPointer();
    const user = userEvent.setup();
    renderBasic();
    await user.click(screen.getByRole('button', { name: 'Open' }));
    expect(screen.getByTestId('popover-body')).toBeInTheDocument();
    await user.keyboard('{Escape}');
    expect(screen.queryByTestId('popover-body')).not.toBeInTheDocument();
  });
});
