export default function Tooltip({ text, children }) {
  return (
    <span className="relative inline-flex items-center group">
      {children}
      <span className="ml-1 cursor-help text-gray-400 text-xs">ⓘ</span>
      <span className="pointer-events-none absolute left-1/2 -translate-x-1/2 bottom-full mb-2 z-50 w-56 rounded bg-gray-900 text-white text-xs px-3 py-2 shadow-lg opacity-0 group-hover:opacity-100 transition-opacity duration-150 whitespace-normal leading-relaxed">
        {text}
      </span>
    </span>
  );
}
