import * as React from 'react';
import type { Meta, StoryObj } from '@storybook/react-vite';

import { ModelPicker, AI_CAPABILITY_OPTIONS } from './ModelPicker';
import type { AICapability } from '@/services/ai/types';

const meta = {
  title: 'Features/AI/ModelPicker',
  component: ModelPicker,
  tags: ['autodocs'],
} satisfies Meta<typeof ModelPicker>;

export default meta;
type Story = StoryObj<typeof meta>;

function ModelPickerDefaultStory(): React.JSX.Element {
  const [value, setValue] = React.useState<AICapability>('CODING');
  return (
    <div className="w-80">
      <ModelPicker value={value} onChange={setValue} />
    </div>
  );
}

export const Default: Story = {
  args: {
    value: 'CODING',
    onChange: () => undefined,
  },
  render: () => <ModelPickerDefaultStory />,
};

export const EveryStateVariant: Story = {
  args: {
    value: 'CODING',
    onChange: () => undefined,
  },
  render: () => (
    <div className="grid max-w-4xl gap-3 md:grid-cols-2">
      {AI_CAPABILITY_OPTIONS.map((option) => (
        <div key={option.value} className="flex flex-col gap-1">
          <span className="text-xs font-medium text-fg-muted">{option.label}</span>
          <ModelPicker value={option.value} onChange={() => undefined} />
        </div>
      ))}
    </div>
  ),
};

export const Disabled: Story = {
  args: {
    value: 'LOCAL_ONLY',
    onChange: () => undefined,
    disabled: true,
  },
  render: () => (
    <div className="w-80">
      <ModelPicker value="LOCAL_ONLY" onChange={() => undefined} disabled />
    </div>
  ),
};
