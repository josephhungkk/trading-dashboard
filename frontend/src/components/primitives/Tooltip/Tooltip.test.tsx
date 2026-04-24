import { describe, it, expect } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import {
  Tooltip,
  TooltipTrigger,
  TooltipContent,
  TooltipProvider,
} from './Tooltip';

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
    <TooltipProvider delayDuration={0} skipDelayDuration={0}>
      <Tooltip>
        <TooltipTrigger>Hover target</TooltipTrigger>
        <TooltipContent>Tooltip body</TooltipContent>
      </Tooltip>
    </TooltipProvider>,
  );
}

describe('Tooltip', () => {
  it('renders the trigger without showing tooltip content initially', () => {
    stubRadixPointer();
    renderBasic();
    expect(screen.getByRole('button', { name: 'Hover target' })).toBeInTheDocument();
    expect(screen.queryByText('Tooltip body')).not.toBeInTheDocument();
  });

  it('shows tooltip content on hover', async () => {
    stubRadixPointer();
    const user = userEvent.setup();
    renderBasic();
    const trigger = screen.getByRole('button', { name: 'Hover target' });
    await user.hover(trigger);
    await waitFor(() => {
      expect(screen.getAllByText('Tooltip body').length).toBeGreaterThan(0);
    });
  });

  it('shows tooltip content when the trigger is focused', async () => {
    stubRadixPointer();
    renderBasic();
    const trigger = screen.getByRole('button', { name: 'Hover target' });
    trigger.focus();
    await waitFor(() => {
      expect(screen.getAllByText('Tooltip body').length).toBeGreaterThan(0);
    });
  });
});
