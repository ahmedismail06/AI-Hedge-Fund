export default function SectorFilterTabs({ sectors = [], selected, onChange }) {
  const all = ['All', ...sectors];
  return (
    <div className="flex flex-wrap gap-2">
      {all.map(s => (
        <button
          key={s}
          onClick={() => onChange(s)}
          className={`px-3 py-1 rounded-full text-sm font-medium border transition-colors ${
            selected === s
              ? 'bg-blue-600 text-white border-blue-600'
              : 'bg-white text-gray-600 border-gray-300 hover:border-blue-400 hover:text-blue-600'
          }`}
        >
          {s}
        </button>
      ))}
    </div>
  );
}
