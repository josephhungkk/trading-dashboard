import { describe, it, expect } from 'vitest';
import { render, screen } from '@testing-library/react';
import { Bell } from 'lucide-react';
import { Icon } from './Icon';

describe('Icon', () => {
  it('renders with default md size classes', () => {
    const { container } = render(<Icon as={Bell} />);
    const svg = container.querySelector('svg');
    expect(svg).not.toBeNull();
    expect(svg?.getAttribute('class') ?? '').toContain('h-5');
    expect(svg?.getAttribute('class') ?? '').toContain('w-5');
  });

  it('sets aria-hidden="true" when no aria-label', () => {
    const { container } = render(<Icon as={Bell} />);
    const svg = container.querySelector('svg');
    expect(svg?.getAttribute('aria-hidden')).toBe('true');
    expect(svg?.getAttribute('role')).toBeNull();
  });

  it('sets role="img" and aria-label when label provided', () => {
    render(<Icon as={Bell} aria-label="notifications" />);
    const img = screen.getByRole('img', { name: 'notifications' });
    expect(img).toBeInTheDocument();
    expect(img.getAttribute('aria-hidden')).toBeNull();
  });

  it('applies correct size class for sm and lg', () => {
    const { container: smContainer } = render(<Icon as={Bell} size="sm" />);
    const smSvg = smContainer.querySelector('svg');
    expect(smSvg?.getAttribute('class') ?? '').toContain('h-4');
    expect(smSvg?.getAttribute('class') ?? '').toContain('w-4');

    const { container: lgContainer } = render(<Icon as={Bell} size="lg" />);
    const lgSvg = lgContainer.querySelector('svg');
    expect(lgSvg?.getAttribute('class') ?? '').toContain('h-6');
    expect(lgSvg?.getAttribute('class') ?? '').toContain('w-6');
  });
});
