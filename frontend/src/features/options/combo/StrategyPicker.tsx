import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '@/components/primitives/Select'

const STRATEGIES = [
  { value: 'VERTICAL', label: 'Vertical' },
  { value: 'CALENDAR', label: 'Calendar' },
  { value: 'DIAGONAL', label: 'Diagonal' },
  { value: 'STRADDLE', label: 'Straddle' },
  { value: 'STRANGLE', label: 'Strangle' },
]

interface Props {
  value: string
  onChange: (v: string) => void
}

export function StrategyPicker({ value, onChange }: Props) {
  return (
    <Select value={value} onValueChange={onChange}>
      <SelectTrigger>
        <SelectValue />
      </SelectTrigger>
      <SelectContent>
        {STRATEGIES.map(s => (
          <SelectItem key={s.value} value={s.value}>
            {s.label}
          </SelectItem>
        ))}
      </SelectContent>
    </Select>
  )
}
