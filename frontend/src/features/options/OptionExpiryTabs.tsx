
interface Props {
  expirations: string[];
  selected: string | null;
  onSelect: (expiry: string) => void;
}

export function OptionExpiryTabs({ expirations, selected, onSelect }: Props) {
  return (
    <div className="flex gap-1 flex-wrap" role="tablist" aria-label="Option expirations">
      {expirations.map((exp) => (
        <button
          key={exp}
          role="tab"
          aria-selected={exp === selected}
          onClick={() => onSelect(exp)}
          className={`rounded px-2 py-0.5 text-xs border transition-colors ${
            exp === selected
              ? 'bg-accent text-accent-foreground border-accent'
              : 'border-border text-muted-foreground hover:border-foreground'
          }`}
          data-testid={`expiry-tab-${exp}`}
        >
          {exp}
        </button>
      ))}
    </div>
  );
}
