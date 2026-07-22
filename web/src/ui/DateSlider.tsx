interface Props {
  dates: string[];
  value: string;
  onChange: (date: string) => void;
}

function relativeLabel(date: string): string {
  const today = new Date();
  const target = new Date(`${date}T12:00:00`);
  const days = Math.round(
    (target.getTime() - new Date(today.toDateString()).getTime()) / 86400000
  );
  if (days === 0) return "today";
  if (days === 1) return "tomorrow";
  if (days === -1) return "yesterday";
  if (days > 1 && days <= 10) return `+${days}d forecast`;
  if (days < 0) return `${days}d`;
  return `+${Math.round(days / 7)}w outlook`;
}

export function DateSlider({ dates, value, onChange }: Props) {
  // A slider with one position is noise — static layers just don't get one.
  if (dates.length < 2) return null;
  const idx = Math.max(0, dates.indexOf(value));
  return (
    <div className="date-slider">
      <input
        type="range"
        min={0}
        max={dates.length - 1}
        step={1}
        value={idx}
        aria-label="Date"
        onChange={(e) => onChange(dates[Number(e.target.value)])}
      />
      <output>
        {value} <span className="date-rel">{relativeLabel(value)}</span>
      </output>
    </div>
  );
}
