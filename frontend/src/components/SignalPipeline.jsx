const STAGES = [
  { key: 'universe',   label: 'Universe',  icon: 'public',       sub: '~800 stocks' },
  { key: 'screened',   label: 'Screened',  icon: 'filter_list',  sub: 'Score ≥ 7.0' },
  { key: 'research',   label: 'In Research', icon: 'query_stats', sub: 'Queued memos' },
  { key: 'memos',      label: 'Memos Done', icon: 'description', sub: 'Completed' },
  { key: 'decisions',  label: 'PM Queue',  icon: 'psychology',   sub: 'Decisions' },
];

const STAGE_COLORS = [
  'var(--text-2)',
  'var(--blue)',
  'var(--amber)',
  'var(--green)',
  'var(--accent)',
];

export default function SignalPipeline({ counts = {}, screenerRunAt, onStageClick }) {
  return (
    <div
      className="rounded-xl px-6 py-4"
      style={{ background: 'var(--surface)', border: '1px solid var(--border)' }}
    >
      <div className="flex items-center gap-1 mb-1">
        <div style={{ fontSize: '9px', fontWeight: 700, letterSpacing: '0.18em', textTransform: 'uppercase', color: 'var(--text-3)', fontFamily: 'Syne' }}>
          Signal Pipeline
        </div>
        {screenerRunAt && (
          <span style={{ marginLeft: '8px', fontSize: '9px', color: 'var(--text-3)', fontFamily: 'JetBrains Mono' }}>
            Last run: {new Date(screenerRunAt).toLocaleDateString('en-US', { month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit' })}
          </span>
        )}
      </div>

      <div className="flex items-center overflow-x-auto no-scrollbar">
        {STAGES.map((stage, i) => {
          const count = counts[stage.key];
          const color = STAGE_COLORS[i];
          const isLast = i === STAGES.length - 1;

          return (
            <div key={stage.key} className="flex items-center flex-shrink-0">
              {/* Stage node */}
              <button
                className="pipeline-node"
                onClick={() => onStageClick?.(stage.key)}
                style={{ minWidth: '80px', padding: '0 8px' }}
              >
                <div
                  className="w-10 h-10 rounded-xl flex items-center justify-center"
                  style={{ background: 'var(--surface-2)', border: `1px solid ${color}` }}
                >
                  <span className="material-symbols-outlined" style={{ fontSize: '18px', color }}>
                    {stage.icon}
                  </span>
                </div>
                <div style={{ fontFamily: 'JetBrains Mono', fontSize: '16px', fontWeight: 800, color, lineHeight: 1 }}>
                  {count != null ? count : '—'}
                </div>
                <div style={{ fontFamily: 'Syne', fontSize: '9px', fontWeight: 700, color: 'var(--text-3)', textAlign: 'center', lineHeight: 1.2 }}>
                  {stage.label}
                </div>
              </button>

              {/* Connector arrow */}
              {!isLast && (
                <div className="flex items-center flex-shrink-0 mx-1">
                  <div style={{ width: '20px', height: '1px', background: 'var(--border-2)' }} />
                  <span className="material-symbols-outlined" style={{ fontSize: '12px', color: 'var(--border-2)', marginLeft: '-4px' }}>
                    arrow_forward_ios
                  </span>
                </div>
              )}
            </div>
          );
        })}
      </div>
    </div>
  );
}
