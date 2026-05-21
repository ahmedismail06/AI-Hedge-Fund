const STAGES = [
  { key: 'universe',  label: 'Universe',    icon: 'public',       sub: '~800 stocks'  },
  { key: 'screened',  label: 'Screened',    icon: 'filter_list',  sub: 'Score ≥ 7.0'  },
  { key: 'research',  label: 'In Research', icon: 'query_stats',  sub: 'Queued memos' },
  { key: 'memos',     label: 'Memos Done',  icon: 'description',  sub: 'Completed'    },
  { key: 'decisions', label: 'PM Queue',    icon: 'psychology',   sub: 'Decisions'    },
];

const STAGE_COLORS = [
  'var(--text-2)',
  'var(--blue)',
  'var(--amber)',
  'var(--green)',
  'var(--accent)',
];

// Rightmost stage with a nonzero count — the furthest the flow has reached
function deriveActiveStage(counts) {
  for (let i = STAGES.length - 1; i >= 0; i--) {
    if ((counts[STAGES[i].key] ?? 0) > 0) return STAGES[i].key;
  }
  return null;
}

export default function SignalPipeline({ counts = {}, screenerRunAt, onStageClick }) {
  const activeStage = deriveActiveStage(counts);

  return (
    <div
      className="rounded-xl px-6 py-4"
      style={{ background: 'var(--surface)', border: '1px solid var(--border)' }}
    >
      <div className="flex items-center gap-1 mb-3">
        <div style={{ fontSize: '9px', fontWeight: 700, letterSpacing: '0.18em', textTransform: 'uppercase', color: 'var(--text-3)', fontFamily: 'Syne' }}>
          Signal Pipeline
        </div>
        {screenerRunAt && (
          <span style={{ marginLeft: '8px', fontSize: '9px', color: 'var(--text-3)', fontFamily: 'JetBrains Mono' }}>
            Last run: {new Date(screenerRunAt).toLocaleDateString('en-US', { month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit' })}
          </span>
        )}
        {activeStage && (
          <span style={{ marginLeft: 'auto', fontSize: '9px', fontWeight: 700, fontFamily: 'Syne', color: 'var(--text-3)' }}>
            Active:{' '}
            <span style={{ color: STAGE_COLORS[STAGES.findIndex(s => s.key === activeStage)] }}>
              {STAGES.find(s => s.key === activeStage)?.label}
            </span>
          </span>
        )}
      </div>

      <div className="flex items-center overflow-x-auto no-scrollbar">
        {STAGES.map((stage, i) => {
          const count = counts[stage.key];
          const color = STAGE_COLORS[i];
          const isLast = i === STAGES.length - 1;
          const isActive = stage.key === activeStage;
          // stages left of the active one are "done"
          const activeIdx = STAGES.findIndex(s => s.key === activeStage);
          const isDone = activeIdx >= 0 && i < activeIdx;
          // connector is "live" up to and including the arrow leading into the active stage
          const connectorLive = activeIdx >= 0 && i < activeIdx;

          return (
            <div key={stage.key} className="flex items-center flex-shrink-0">
              <button
                className="pipeline-node"
                onClick={() => onStageClick?.(stage.key)}
                style={{ minWidth: '80px', padding: '0 8px', position: 'relative' }}
              >
                {/* Icon box */}
                <div
                  className="w-10 h-10 rounded-xl flex items-center justify-center"
                  style={{
                    background: isActive ? `color-mix(in srgb, ${color} 15%, var(--surface-2))` : 'var(--surface-2)',
                    border: `1px solid ${isActive ? color : isDone ? color : 'var(--border-2)'}`,
                    boxShadow: isActive ? `0 0 12px -2px ${color}` : 'none',
                    transition: 'box-shadow 0.3s, border-color 0.3s',
                  }}
                >
                  <span
                    className="material-symbols-outlined"
                    style={{
                      fontSize: '18px',
                      color: isActive || isDone ? color : 'var(--text-3)',
                      transition: 'color 0.3s',
                    }}
                  >
                    {isDone ? 'check_circle' : stage.icon}
                  </span>
                </div>

                {/* Count */}
                <div
                  style={{
                    fontFamily: 'JetBrains Mono',
                    fontSize: '16px',
                    fontWeight: 800,
                    color: isActive ? color : isDone ? color : 'var(--text-3)',
                    lineHeight: 1,
                    transition: 'color 0.3s',
                  }}
                >
                  {count != null ? count : '—'}
                </div>

                {/* Label */}
                <div
                  style={{
                    fontFamily: 'Syne',
                    fontSize: '9px',
                    fontWeight: 700,
                    color: isActive ? 'var(--text-2)' : 'var(--text-3)',
                    textAlign: 'center',
                    lineHeight: 1.2,
                  }}
                >
                  {stage.label}
                </div>

                {/* Active dot */}
                {isActive && (
                  <div
                    style={{
                      position: 'absolute',
                      top: '0px',
                      right: '10px',
                      width: '6px',
                      height: '6px',
                      borderRadius: '50%',
                      background: color,
                      boxShadow: `0 0 6px 1px ${color}`,
                      animation: 'pulse-dot 2s ease-in-out infinite',
                    }}
                  />
                )}
              </button>

              {/* Connector */}
              {!isLast && (
                <div className="flex items-center flex-shrink-0 mx-1">
                  <div
                    style={{
                      width: '20px',
                      height: '2px',
                      background: connectorLive ? STAGE_COLORS[i] : 'var(--border-2)',
                      transition: 'background 0.3s',
                      opacity: connectorLive ? 0.6 : 1,
                    }}
                  />
                  <span
                    className="material-symbols-outlined"
                    style={{
                      fontSize: '12px',
                      color: connectorLive ? STAGE_COLORS[i] : 'var(--border-2)',
                      marginLeft: '-4px',
                      transition: 'color 0.3s',
                    }}
                  >
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
