interface Option {
  id: string;
  label: string;
}

interface Props {
  options: Option[];
  selected: string[];
  onChange: (ids: string[]) => void;
  disabled?: boolean;
  placeholder?: string;
}

// Checkbox dropdown (uses native <details> for open/close — no click-away JS).
export function MultiSelect({ options, selected, onChange, disabled, placeholder = "Select…" }: Props) {
  const allSelected = options.length > 0 && selected.length === options.length;
  const toggle = (id: string) =>
    onChange(selected.includes(id) ? selected.filter((x) => x !== id) : [...selected, id]);
  const toggleAll = () => onChange(allSelected ? [] : options.map((o) => o.id));

  const summary =
    selected.length === 0
      ? placeholder
      : allSelected
      ? "All selected"
      : selected.length === 1
      ? options.find((o) => o.id === selected[0])?.label ?? "1 selected"
      : `${selected.length} selected`;

  return (
    <details className={`multiselect${disabled ? " disabled" : ""}`}>
      <summary onClick={(e) => disabled && e.preventDefault()}>
        <span className="ms-summary">{summary}</span>
        <span className="ms-caret">▾</span>
      </summary>
      <div className="ms-panel">
        <label className="ms-all">
          <input type="checkbox" checked={allSelected} onChange={toggleAll} disabled={disabled} />
          Select All
        </label>
        {options.map((o) => (
          <label key={o.id}>
            <input
              type="checkbox"
              checked={selected.includes(o.id)}
              onChange={() => toggle(o.id)}
              disabled={disabled}
            />
            {o.label}
          </label>
        ))}
      </div>
    </details>
  );
}
