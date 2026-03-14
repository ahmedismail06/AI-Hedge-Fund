// ThesisBlock — renders bull thesis, bear thesis, and key risks as labeled bullet lists

function BulletList({ items, colorClass }) {
  if (!items || items.length === 0) return <p className="text-sm text-gray-400 italic">None provided</p>;
  return (
    <ul className="mt-1 space-y-1">
      {items.map((point, i) => (
        <li key={i} className={`flex gap-2 text-sm ${colorClass}`}>
          <span className="mt-0.5 shrink-0">•</span>
          <span>{point}</span>
        </li>
      ))}
    </ul>
  );
}

export default function ThesisBlock({ bullThesis, bearThesis, keyRisks }) {
  return (
    <div className="grid grid-cols-1 gap-4 sm:grid-cols-3">
      <div>
        <h4 className="text-xs font-semibold uppercase tracking-wide text-green-700">
          Bull Thesis
        </h4>
        <BulletList items={bullThesis} colorClass="text-green-900" />
      </div>

      <div>
        <h4 className="text-xs font-semibold uppercase tracking-wide text-red-700">
          Bear Thesis
        </h4>
        <BulletList items={bearThesis} colorClass="text-red-900" />
      </div>

      <div>
        <h4 className="text-xs font-semibold uppercase tracking-wide text-orange-700">
          Key Risks
        </h4>
        <BulletList items={keyRisks} colorClass="text-orange-900" />
      </div>
    </div>
  );
}
