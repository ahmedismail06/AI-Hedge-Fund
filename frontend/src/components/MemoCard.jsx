// MemoCard — displays a full investment memo with approve/reject/watchlist actions

import { useState } from 'react';
import ConvictionBadge from './ConvictionBadge';
import ThesisBlock from './ThesisBlock';
import { updateMemoStatus } from '../api/research';

const VERDICT_COLORS = {
  LONG: 'bg-green-50 text-green-800 border-green-200',
  SHORT: 'bg-red-50 text-red-800 border-red-200',
  AVOID: 'bg-gray-50 text-gray-700 border-gray-200',
};

function FinancialHealthRow({ label, value }) {
  return (
    <div className="flex items-center justify-between py-1 text-sm border-b border-gray-100 last:border-0">
      <span className="text-gray-500">{label}</span>
      <span className="font-medium text-gray-800 capitalize">{value ?? '—'}</span>
    </div>
  );
}

export default function MemoCard({ memo, onStatusChange }) {
  const [status, setStatus] = useState(memo?.status ?? 'PENDING');
  const [loading, setLoading] = useState(null); // 'APPROVED' | 'REJECTED' | 'WATCHLIST'

  if (!memo) return null;

  const memoData = memo.memo_json ?? memo; // support both raw API response and stored row
  const memoId = memo.id;

  async function handleAction(newStatus) {
    if (!memoId) return;
    setLoading(newStatus);
    try {
      await updateMemoStatus(memoId, newStatus);
      setStatus(newStatus);
      if (onStatusChange) onStatusChange(memoId, newStatus);
    } finally {
      setLoading(null);
    }
  }

  const verdictClass = VERDICT_COLORS[memoData.verdict] ?? VERDICT_COLORS.AVOID;
  const fh = memoData.financial_health ?? {};

  return (
    <div className="rounded-xl border border-gray-200 bg-white shadow-sm overflow-hidden">
      {/* Header */}
      <div className={`flex items-start justify-between gap-4 px-5 py-4 border-b ${verdictClass}`}>
        <div>
          <div className="flex items-center gap-3">
            <h2 className="text-2xl font-bold tracking-tight">{memoData.ticker}</h2>
            <span className="rounded-md border px-2 py-0.5 text-xs font-semibold uppercase tracking-wide">
              {memoData.verdict}
            </span>
          </div>
          <p className="mt-0.5 text-xs text-gray-500">{memoData.date}</p>
        </div>
        <ConvictionBadge score={memoData.conviction_score} />
      </div>

      <div className="px-5 py-4 space-y-5">
        {/* Overview */}
        <div>
          <h3 className="text-xs font-semibold uppercase tracking-wide text-gray-400 mb-1">
            Company Overview
          </h3>
          <p className="text-sm text-gray-700">{memoData.company_overview}</p>
        </div>

        {/* Bull / Bear / Risks */}
        <ThesisBlock
          bullThesis={memoData.bull_thesis}
          bearThesis={memoData.bear_thesis}
          keyRisks={memoData.key_risks}
        />

        {/* Catalysts */}
        {memoData.catalysts?.length > 0 && (
          <div>
            <h3 className="text-xs font-semibold uppercase tracking-wide text-gray-400 mb-1">
              Catalysts
            </h3>
            <ul className="list-disc list-inside space-y-0.5">
              {memoData.catalysts.map((c, i) => (
                <li key={i} className="text-sm text-gray-700">{c}</li>
              ))}
            </ul>
          </div>
        )}

        {/* Financial Health */}
        <div>
          <h3 className="text-xs font-semibold uppercase tracking-wide text-gray-400 mb-1">
            Financial Health
          </h3>
          <div className="rounded-lg border border-gray-100 divide-y divide-gray-100 bg-gray-50 px-3 py-1">
            <FinancialHealthRow label="Revenue Trend" value={fh.revenue_trend} />
            <FinancialHealthRow label="Margin Trend" value={fh.margin_trend} />
            <FinancialHealthRow label="Debt Level" value={fh.debt_level} />
            <FinancialHealthRow label="Free Cash Flow" value={fh.fcf} />
          </div>
        </div>

        {/* Macro Sensitivity */}
        <div>
          <h3 className="text-xs font-semibold uppercase tracking-wide text-gray-400 mb-1">
            Macro Sensitivity
          </h3>
          <p className="text-sm text-gray-700">{memoData.macro_sensitivity}</p>
        </div>

        {/* Summary */}
        <div>
          <h3 className="text-xs font-semibold uppercase tracking-wide text-gray-400 mb-1">
            Summary
          </h3>
          <p className="text-sm text-gray-700 italic">{memoData.summary}</p>
        </div>

        {/* Suggested Size */}
        <p className="text-xs text-gray-500">
          Suggested position size:{' '}
          <span className="font-semibold text-gray-700 capitalize">
            {memoData.suggested_position_size ?? '—'}
          </span>
        </p>
      </div>

      {/* Action Bar */}
      <div className="flex items-center gap-3 border-t border-gray-100 bg-gray-50 px-5 py-3">
        {status !== 'PENDING' && (
          <span className="mr-auto text-xs font-medium text-gray-500 uppercase tracking-wide">
            Status: {status}
          </span>
        )}
        <button
          disabled={!memoId || !!loading || status === 'APPROVED'}
          onClick={() => handleAction('APPROVED')}
          className="rounded-md bg-green-600 px-3 py-1.5 text-xs font-semibold text-white hover:bg-green-700 disabled:opacity-40 disabled:cursor-not-allowed transition-colors"
        >
          {loading === 'APPROVED' ? 'Approving…' : 'Approve'}
        </button>
        <button
          disabled={!memoId || !!loading || status === 'REJECTED'}
          onClick={() => handleAction('REJECTED')}
          className="rounded-md bg-red-600 px-3 py-1.5 text-xs font-semibold text-white hover:bg-red-700 disabled:opacity-40 disabled:cursor-not-allowed transition-colors"
        >
          {loading === 'REJECTED' ? 'Rejecting…' : 'Reject'}
        </button>
        <button
          disabled={!memoId || !!loading || status === 'WATCHLIST'}
          onClick={() => handleAction('WATCHLIST')}
          className="rounded-md bg-yellow-500 px-3 py-1.5 text-xs font-semibold text-white hover:bg-yellow-600 disabled:opacity-40 disabled:cursor-not-allowed transition-colors"
        >
          {loading === 'WATCHLIST' ? 'Adding…' : 'Watchlist'}
        </button>
      </div>
    </div>
  );
}
