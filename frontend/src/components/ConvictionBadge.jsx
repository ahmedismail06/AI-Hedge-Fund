// ConvictionBadge — color-coded badge showing conviction score (0-10) from the Research Agent

export default function ConvictionBadge({ score }) {
  const num = parseFloat(score);

  let colorClass;
  if (num >= 8) {
    colorClass = 'bg-green-100 text-green-800 ring-green-300';
  } else if (num >= 6) {
    colorClass = 'bg-yellow-100 text-yellow-800 ring-yellow-300';
  } else {
    colorClass = 'bg-red-100 text-red-800 ring-red-300';
  }

  return (
    <span
      className={`inline-flex items-center rounded-full px-2.5 py-0.5 text-xs font-semibold ring-1 ring-inset ${colorClass}`}
    >
      {isNaN(num) ? '—' : num.toFixed(1)} / 10
    </span>
  );
}
