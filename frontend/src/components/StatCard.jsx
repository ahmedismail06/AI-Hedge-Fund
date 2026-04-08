import Tooltip from './Tooltip';

const STATUS_COLORS = {
  ok: 'bg-green-50 border-green-200',
  warn: 'bg-yellow-50 border-yellow-200',
  critical: 'bg-red-50 border-red-200',
  neutral: 'bg-white border-gray-200',
};

const STATUS_DOT = {
  ok: 'bg-green-400',
  warn: 'bg-yellow-400',
  critical: 'bg-red-500',
  neutral: 'bg-gray-300',
};

export default function StatCard({ label, plainLabel, value, tooltip, delta, status = 'neutral' }) {
  const cardClass = STATUS_COLORS[status] || STATUS_COLORS.neutral;
  const dotClass = STATUS_DOT[status] || STATUS_DOT.neutral;

  return (
    <div className={`relative rounded-xl border p-4 ${cardClass}`}>
      <div className="absolute top-3 right-3">
        <span className={`inline-block w-2.5 h-2.5 rounded-full ${dotClass}`} />
      </div>
      <div className="text-xs font-medium text-gray-500 uppercase tracking-wide mb-0.5">
        {tooltip ? (
          <Tooltip text={tooltip}>{label}</Tooltip>
        ) : label}
      </div>
      {plainLabel && (
        <div className="text-xs text-gray-400 mb-2">{plainLabel}</div>
      )}
      <div className="text-2xl font-bold text-gray-900 mt-1">{value ?? '—'}</div>
      {delta && (
        <div className="text-xs text-gray-500 mt-1">{delta}</div>
      )}
    </div>
  );
}
