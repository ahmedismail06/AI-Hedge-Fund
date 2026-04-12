import Tooltip from './Tooltip';

const STATUS_DOT = {
  ok:       { cls: 'dot-green', accentVar: 'var(--green)' },
  warn:     { cls: 'dot-amber', accentVar: 'var(--amber)' },
  critical: { cls: 'dot-red',   accentVar: 'var(--red)' },
  neutral:  { cls: 'dot-gray',  accentVar: null },
};

export default function StatCard({ label, plainLabel, value, tooltip, delta, status = 'neutral' }) {
  const s = STATUS_DOT[status] || STATUS_DOT.neutral;

  return (
    <div
      className="relative rounded-lg p-4 card-hover"
      style={{ background: 'var(--surface)', border: '1px solid var(--border)' }}
    >
      {/* Status dot */}
      <span className={`absolute top-3 right-3 w-2 h-2 rounded-full ${s.cls}`} />

      {/* Label */}
      <div className="section-label mb-1">
        {tooltip ? <Tooltip text={tooltip}>{label}</Tooltip> : label}
      </div>
      {plainLabel && (
        <div className="text-[11px] mb-1.5" style={{ color: 'var(--text-2)' }}>{plainLabel}</div>
      )}

      {/* Value */}
      <div
        className="text-2xl font-semibold mt-1 font-data"
        style={{ color: 'var(--text)', fontFamily: 'JetBrains Mono' }}
      >
        {value ?? '—'}
      </div>

      {/* Delta */}
      {delta && (
        <div className="text-[11px] mt-1 font-data" style={{ color: 'var(--text-2)' }}>
          {delta}
        </div>
      )}

      {/* Bottom accent line for non-neutral status */}
      {status !== 'neutral' && s.accentVar && (
        <div
          className="absolute bottom-0 left-0 h-[2px] w-full rounded-b-lg"
          style={{
            background: `linear-gradient(90deg, ${s.accentVar} 0%, transparent 100%)`,
            opacity: 0.5,
          }}
        />
      )}
    </div>
  );
}
