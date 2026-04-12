import { createPortal } from 'react-dom';

export default function ConfirmDialog({
  title,
  message,
  onConfirm,
  onCancel,
  confirmLabel = 'Confirm',
  destructive = false,
}) {
  return createPortal(
    <div className="fixed inset-0 z-[100] flex items-center justify-center">
      {/* Backdrop */}
      <div
        className="absolute inset-0"
        style={{ background: 'var(--modal-backdrop)' }}
        onClick={onCancel}
      />

      {/* Dialog */}
      <div
        className="relative w-full max-w-md mx-4 p-6 rounded-xl animate-slide-down"
        style={{
          background:  'var(--surface)',
          border:      destructive
            ? '1px solid var(--red-border)'
            : '1px solid var(--accent-ring)',
          boxShadow:   'var(--card-shadow)',
        }}
      >
        <h2
          className="text-base font-bold mb-2"
          style={{ color: 'var(--text)', fontFamily: 'Syne' }}
        >
          {title}
        </h2>
        <p className="text-sm mb-6 leading-relaxed" style={{ color: 'var(--text-2)' }}>
          {message}
        </p>

        <div className="flex justify-end gap-3">
          <button
            onClick={onCancel}
            className="px-4 py-2 text-[12px] font-bold rounded-md transition-all"
            style={{
              background: 'var(--surface-2)',
              border:     '1px solid var(--border)',
              color:      'var(--text-2)',
              fontFamily: 'Syne',
            }}
            onMouseEnter={e => e.currentTarget.style.borderColor = 'var(--border-2)'}
            onMouseLeave={e => e.currentTarget.style.borderColor = 'var(--border)'}
          >
            Cancel
          </button>
          <button
            onClick={onConfirm}
            className="px-4 py-2 text-[12px] font-bold rounded-md transition-all"
            style={
              destructive
                ? { background: 'var(--red-bg)',   border: '1px solid var(--red-border)',   color: 'var(--red)',   fontFamily: 'Syne' }
                : { background: 'var(--accent-muted)', border: '1px solid var(--accent-ring)', color: 'var(--accent)', fontFamily: 'Syne' }
            }
            onMouseEnter={e => (e.currentTarget.style.opacity = '0.75')}
            onMouseLeave={e => (e.currentTarget.style.opacity = '1')}
          >
            {confirmLabel}
          </button>
        </div>
      </div>
    </div>,
    document.body
  );
}
