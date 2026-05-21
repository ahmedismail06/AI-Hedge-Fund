// Primitive skeleton shapes — compose these inside panel layouts
export function SkeletonLine({ width = '100%', height = 10, style = {} }) {
  return <div className="skeleton" style={{ width, height, borderRadius: 4, ...style }} />;
}

export function SkeletonBlock({ height = 80, style = {} }) {
  return <div className="skeleton" style={{ width: '100%', height, borderRadius: 8, ...style }} />;
}

// A row that mimics a table row: ticker + two value columns
export function SkeletonRow() {
  return (
    <div style={{ display: 'flex', alignItems: 'center', gap: 10, padding: '8px 12px' }}>
      <SkeletonLine width={44} height={12} />
      <div style={{ flex: 1 }} />
      <SkeletonLine width={52} height={10} />
      <SkeletonLine width={42} height={10} />
    </div>
  );
}

// A stat card skeleton: label + big number
export function SkeletonStat({ style = {} }) {
  return (
    <div
      className="panel"
      style={{ padding: '14px 16px', display: 'flex', flexDirection: 'column', gap: 8, ...style }}
    >
      <SkeletonLine width={64} height={8} />
      <SkeletonLine width={88} height={22} />
      <SkeletonLine width={48} height={8} />
    </div>
  );
}

// A panel skeleton with a header and N rows of content
export function SkeletonPanel({ rows = 4, label = '', style = {} }) {
  return (
    <div className="panel" style={style}>
      <div className="panel-header">
        {label
          ? <span style={{ fontSize: 9, fontWeight: 700, letterSpacing: '0.18em', textTransform: 'uppercase', color: 'var(--text-3)', fontFamily: 'Syne' }}>{label}</span>
          : <SkeletonLine width={72} height={8} />
        }
      </div>
      <div style={{ padding: '4px 0' }}>
        {Array.from({ length: rows }, (_, i) => <SkeletonRow key={i} />)}
      </div>
    </div>
  );
}

// A chart area placeholder
export function SkeletonChart({ height = 160, style = {} }) {
  return (
    <div style={{ padding: '12px', ...style }}>
      <SkeletonBlock height={height} />
    </div>
  );
}
