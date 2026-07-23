interface Props {
  dates: string[];
  value: string;
  onChange: (date: string) => void;
  /** Hourly player: show a play button and hour-resolution labels. */
  playing?: boolean;
  onPlayToggle?: () => void;
}

/** "YYYY-MM-DDTHHMM" (hourly frame) or "YYYY-MM-DD" (daily). */
export function parseFrame(key: string): Date {
  if (key.length > 10) {
    return new Date(
      `${key.slice(0, 10)}T${key.slice(11, 13)}:${key.slice(13, 15) || "00"}:00Z`
    );
  }
  return new Date(`${key}T12:00:00`);
}

function label(key: string): { main: string; rel: string } {
  const t = parseFrame(key);
  if (key.length > 10) {
    const main = t.toLocaleString(undefined, {
      weekday: "short",
      hour: "numeric",
      minute: t.getMinutes() ? "2-digit" : undefined,
    });
    const hrs = Math.round((t.getTime() - Date.now()) / 3600000);
    return { main, rel: hrs === 0 ? "now" : hrs > 0 ? `+${hrs}h` : `${hrs}h` };
  }
  const days = Math.round(
    (t.getTime() - new Date(new Date().toDateString()).getTime()) / 86400000
  );
  const rel =
    days === 0 ? "today" : days === 1 ? "tomorrow" : days === -1 ? "yesterday"
    : days > 1 && days <= 10 ? `+${days}d forecast`
    : days < 0 ? `${days}d` : `+${Math.round(days / 7)}w outlook`;
  return { main: key, rel };
}

export function DateSlider({ dates, value, onChange, playing, onPlayToggle }: Props) {
  // A slider with one position is noise — static layers just don't get one.
  if (dates.length < 2) return null;
  const idx = Math.max(0, dates.indexOf(value));
  const l = label(value);
  return (
    <div className="date-slider">
      {onPlayToggle && (
        <button className="radar-play" onClick={onPlayToggle} aria-label={playing ? "Pause" : "Play"}>
          {playing ? "⏸" : "▶"}
        </button>
      )}
      <input
        type="range"
        min={0}
        max={dates.length - 1}
        step={1}
        value={idx}
        aria-label="Time"
        onChange={(e) => onChange(dates[Number(e.target.value)])}
      />
      <output>
        {l.main} <span className="date-rel">{l.rel}</span>
      </output>
    </div>
  );
}
