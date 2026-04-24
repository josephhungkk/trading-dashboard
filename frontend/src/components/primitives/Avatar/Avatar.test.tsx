import { describe, it, expect } from 'vitest';
import { render, screen } from '@testing-library/react';
import { Avatar, AvatarFallback, initials } from './Avatar';

describe('initials()', () => {
  it('returns first+last initials for multi-word label', () => {
    expect(initials('Ada Lovelace')).toBe('AL');
  });

  it('returns first two letters for single-word label', () => {
    expect(initials('Alice')).toBe('AL');
  });

  it('returns empty string for whitespace-only label', () => {
    expect(initials('  ')).toBe('');
  });

  it('skips middle names and uses first+last initial', () => {
    expect(initials('Katherine Johnson Goble Moore')).toBe('KM');
  });

  it('uppercases lowercase input', () => {
    expect(initials('grace hopper')).toBe('GH');
  });
});

describe('Avatar fallback', () => {
  it('renders fallback text', () => {
    render(
      <Avatar>
        <AvatarFallback>AL</AvatarFallback>
      </Avatar>,
    );
    expect(screen.getByText('AL')).toBeInTheDocument();
  });
});
