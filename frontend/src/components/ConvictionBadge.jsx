export default function ConvictionBadge({ score }) {
  const num = parseFloat(score);

  // CSS variable strings resolve at render time — works as inline style values
  let bgVar, colorVar, borderVar;
  if (isNaN(num)) {
    bgVar = 'var(--surface-2)'; colorVar = 'var(--text-2)'; borderVar = 'var(--border)';
  } else if (num >= 8) {
    bgVar = 'var(--green-bg)'; colorVar = 'var(--green)'; borderVar = 'var(--green-border)';
  } else if (num >= 6) {
    bgVar = 'var(--accent-muted)'; colorVar = 'var(--accent)'; borderVar = 'var(--accent-ring)';
  } else {
    bgVar = 'var(--red-bg)'; colorVar = 'var(--red)'; borderVar = 'transparent';
  }

  return (
    <span
      className="inline-flex items-center px-2 py-0.5 rounded-sm text-[10px] font-semibold font-data"
      style={{
        background: bgVar,
        color:      colorVar,
        border:     `1px solid ${borderVar}`,
        fontFamily: 'JetBrains Mono',
      }}
    >
      {isNaN(num) ? '—' : num.toFixed(1)}
      <span style={{ opacity: 0.45, marginLeft: 2 }}>/10</span>
    </span>
  );
}
