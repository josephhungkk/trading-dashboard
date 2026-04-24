import type { Meta, StoryObj } from '@storybook/react-vite';
import { Avatar, AvatarFallback, AvatarImage, initials } from './Avatar';

const meta = {
  title: 'Primitives/Avatar',
  component: Avatar,
  tags: ['autodocs'],
} satisfies Meta<typeof Avatar>;

export default meta;
type Story = StoryObj<typeof meta>;

export const WithImage: Story = {
  render: () => (
    <Avatar>
      <AvatarImage src="https://i.pravatar.cc/80?img=3" alt="ada" />
      <AvatarFallback>{initials('Ada Lovelace')}</AvatarFallback>
    </Avatar>
  ),
};

export const Fallback: Story = {
  render: () => (
    <Avatar>
      <AvatarFallback>{initials('Ada Lovelace')}</AvatarFallback>
    </Avatar>
  ),
};

export const LongLabelInitials: Story = {
  render: () => (
    <Avatar>
      <AvatarFallback>
        {initials('Katherine Johnson Goble Moore')}
      </AvatarFallback>
    </Avatar>
  ),
};

export const SingleWord: Story = {
  render: () => (
    <Avatar>
      <AvatarFallback>{initials('Grace')}</AvatarFallback>
    </Avatar>
  ),
};
