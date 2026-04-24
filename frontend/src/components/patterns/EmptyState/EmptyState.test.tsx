import { describe, it, expect, vi } from 'vitest';
import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { Inbox } from 'lucide-react';
import { EmptyState } from './EmptyState';

describe('EmptyState', () => {
  it('renders title', () => {
    render(<EmptyState title="Nothing here" />);
    expect(screen.getByRole('heading', { name: 'Nothing here' })).toBeInTheDocument();
  });

  it('renders description when provided', () => {
    render(<EmptyState title="t" description="some description text" />);
    expect(screen.getByText('some description text')).toBeInTheDocument();
  });

  it('does not render description element when omitted', () => {
    render(<EmptyState title="t" />);
    expect(screen.queryByText(/description/i)).not.toBeInTheDocument();
  });

  it('renders icon when provided', () => {
    const { container } = render(<EmptyState icon={Inbox} title="t" />);
    expect(container.querySelector('svg')).toBeInTheDocument();
  });

  it('fires action onClick when action button clicked', async () => {
    const user = userEvent.setup();
    const onClick = vi.fn();
    render(<EmptyState title="t" action={{ label: 'Create', onClick }} />);
    await user.click(screen.getByRole('button', { name: 'Create' }));
    expect(onClick).toHaveBeenCalledTimes(1);
  });

  it('does not render action button when action is omitted', () => {
    render(<EmptyState title="t" />);
    expect(screen.queryByRole('button')).not.toBeInTheDocument();
  });
});
