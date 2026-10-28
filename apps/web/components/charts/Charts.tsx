import type { SeriesPoint } from '@/lib/types';

const AXIS = 'rgb(var(--text-3))';
const LABEL = 'rgb(var(--text-2))';
const ACCENT = 'rgb(var(--accent))';
const GRID = 'var(--border)';

function niceMax(values: number[]): number {
  const max = Math.max(...values, 0);
  if (max <= 0) return 1;
  const magnitude = 10 ** Math.floor(Math.log10(max));
  return Math.ceil(max / magnitude) * magnitude;
}

/** Horizontal bars. Scales with its container; text stays proportional. */
export function BarChartSvg({ data, label }: { data: SeriesPoint[]; label: string }) {
  if (data.length === 0) return <EmptyChart />;

  const rowHeight = 30;
  const width = 620;
  const labelWidth = 168;
  const valueWidth = 46;
  const trackWidth = width - labelWidth - valueWidth;
  const height = data.length * rowHeight + 10;
  const max = niceMax(data.map((d) => d.value));

  return (
    <svg
      viewBox={`0 0 ${width} ${height}`}
      className="h-auto w-full"
      role="img"
      aria-label={`${label}: bar chart of ${data.length} values`}
    >
      {data.map((point, i) => {
        const y = i * rowHeight + 5;
        const barWidth = Math.max(2, (point.value / max) * trackWidth);
        return (
          <g key={`${point.name}-${i}`}>
            <text
              x={labelWidth - 12}
              y={y + 15}
              textAnchor="end"
              fill={LABEL}
              fontSize="12"
              fontFamily="ui-sans-serif, system-ui, sans-serif"
            >
              {point.name.length > 26 ? `${point.name.slice(0, 25)}…` : point.name}
            </text>
            <rect x={labelWidth} y={y + 4} width={trackWidth} height={14} rx={3} fill={GRID} />
            <rect x={labelWidth} y={y + 4} width={barWidth} height={14} rx={3} fill={ACCENT} opacity="0.85" />
            <text
              x={labelWidth + trackWidth + 8}
              y={y + 15}
              fill={LABEL}
              fontSize="12"
              fontFamily="ui-monospace, monospace"
            >
              {point.value}
            </text>
          </g>
        );
      })}
    </svg>
  );
}

/** Line with a flat area wash underneath. */
export function LineChartSvg({ data, label }: { data: SeriesPoint[]; label: string }) {
  if (data.length === 0) return <EmptyChart />;

  const width = 620;
  const height = 230;
  const padLeft = 44;
  const padRight = 14;
  const padTop = 14;
  const padBottom = 30;
  const plotWidth = width - padLeft - padRight;
  const plotHeight = height - padTop - padBottom;
  const max = niceMax(data.map((d) => d.value));
  const step = data.length > 1 ? plotWidth / (data.length - 1) : 0;

  const points = data.map((point, i) => {
    const x = data.length > 1 ? padLeft + i * step : padLeft + plotWidth / 2;
    const y = padTop + plotHeight - (point.value / max) * plotHeight;
    return { ...point, x, y };
  });

  const line = points.map((p, i) => `${i === 0 ? 'M' : 'L'}${p.x.toFixed(1)},${p.y.toFixed(1)}`).join(' ');
  const first = points[0];
  const last = points[points.length - 1];
  const area =
    first && last
      ? `${line} L${last.x.toFixed(1)},${padTop + plotHeight} L${first.x.toFixed(1)},${padTop + plotHeight} Z`
      : '';

  const ticks = [0, 0.5, 1];
  const labelEvery = Math.max(1, Math.ceil(data.length / 6));

  return (
    <svg
      viewBox={`0 0 ${width} ${height}`}
      className="h-auto w-full"
      role="img"
      aria-label={`${label}: line chart of ${data.length} points`}
    >
      {ticks.map((t) => {
        const y = padTop + plotHeight - t * plotHeight;
        return (
          <g key={t}>
            <line x1={padLeft} x2={width - padRight} y1={y} y2={y} stroke={GRID} strokeWidth="1" />
            <text
              x={padLeft - 8}
              y={y + 4}
              textAnchor="end"
              fill={AXIS}
              fontSize="11"
              fontFamily="ui-monospace, monospace"
            >
              {Math.round(max * t)}
            </text>
          </g>
        );
      })}

      {area ? <path d={area} fill={ACCENT} opacity="0.12" /> : null}
      <path d={line} fill="none" stroke={ACCENT} strokeWidth="2" strokeLinejoin="round" strokeLinecap="round" />

      {points.map((point, i) => (
        <g key={`${point.name}-${i}`}>
          <circle cx={point.x} cy={point.y} r="2.6" fill={ACCENT} />
          {i % labelEvery === 0 || i === points.length - 1 ? (
            <text
              x={point.x}
              y={height - 10}
              textAnchor={i === 0 ? 'start' : i === points.length - 1 ? 'end' : 'middle'}
              fill={AXIS}
              fontSize="11"
              fontFamily="ui-sans-serif, system-ui, sans-serif"
            >
              {point.name.length > 12 ? `${point.name.slice(0, 11)}…` : point.name}
            </text>
          ) : null}
        </g>
      ))}
    </svg>
  );
}

function EmptyChart() {
  return <p className="py-8 text-center text-[13px] text-ink-faint">No data in this series yet.</p>;
}
