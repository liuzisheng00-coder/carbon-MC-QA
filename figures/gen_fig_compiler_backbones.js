const {COLORS, csv, text, line, rect, multiline, saveFigure} = require('./paper_plot_style');

async function main() {
  const data = csv('compiler_backbones.csv').map(row => Object.fromEntries(Object.entries(row).map(([key, value]) => [key, key === 'model' ? value : +value])));
  const metrics = [
    {key: 'pass_rate', sd: 'pass_sd', label: 'Pass', color: COLORS.blue},
    {key: 'numeric_accuracy', sd: 'numeric_sd', label: 'Numeric', color: COLORS.teal},
    {key: 'status_accuracy', sd: 'status_sd', label: 'Status', color: COLORS.orange}
  ];
  const labelLines = {
    'qwen3.7-flash': ['qwen3.7', 'flash'],
    'deepseek-chat': ['deepseek', 'chat'],
    'qwen3.7-plus': ['qwen3.7', 'plus'],
    'deepseek-v4-pro': ['deepseek', 'v4-pro'],
    'gemini-2.5-flash': ['gemini-2.5', 'flash'],
    'gemini-3-flash-preview': ['gemini-3', 'flash-preview']
  };
  let body = '';
  const left = 105, middle = 780, right = 1360, top = 75, bottom = 550, chartH = bottom - top;
  for (let tick = 0; tick <= 100; tick += 20) {
    const y = bottom - tick / 100 * chartH;
    body += line(left, y, middle - 45, y, {stroke: tick === 0 ? COLORS.ink : COLORS.grid, sw: tick === 0 ? 2 : 1, dash: tick === 0 ? '' : '5 5'});
    body += text(left - 12, y + 6, `${tick}`, {size: 16, anchor: 'end'});
  }
  body += text(34, 315, 'Accuracy (%)', {size: 19, anchor: 'middle'}).replace('<text ', '<text transform="rotate(-90 34 315)" ');
  const step = (middle - 55 - left) / data.length;
  data.forEach((row, i) => {
    const cx = left + step * (i + 0.5);
    metrics.forEach((metric, j) => {
      const bw = 21, x = cx + (j - 1) * 25 - bw / 2;
      const y = bottom - row[metric.key] / 100 * chartH;
      body += rect(x, y, bw, bottom - y, {fill: metric.color, stroke: metric.color, rx: 1});
      const err = row[metric.sd] / 100 * chartH;
      body += line(x + bw / 2, y - err, x + bw / 2, y + err, {stroke: COLORS.ink, sw: 1.1});
      body += line(x + bw / 2 - 4, y - err, x + bw / 2 + 4, y - err, {stroke: COLORS.ink, sw: 1.1});
    });
    const [first, second] = labelLines[row.model];
    body += text(cx, 586, first, {size: 14, anchor: 'middle', weight: i < 3 ? 700 : 400});
    body += text(cx, 604, second, {size: 14, anchor: 'middle', weight: i < 3 ? 700 : 400});
  });
  metrics.forEach((metric, i) => {
    const x = 110 + i * 110;
    body += rect(x, 24, 18, 14, {fill: metric.color, stroke: metric.color, rx: 1});
    body += text(x + 27, 37, metric.label, {size: 15});
  });
  const pLeft = 890, pRight = 1330, pTop = 110, pBottom = 520;
  const minLog = Math.log10(2000), maxLog = Math.log10(60000);
  [2, 5, 10, 20, 50].forEach(seconds => {
    const x = pLeft + (Math.log10(seconds * 1000) - minLog) / (maxLog - minLog) * (pRight - pLeft);
    body += line(x, pTop, x, pBottom, {stroke: COLORS.grid, sw: 1, dash: '4 4'});
    body += text(x, pBottom + 25, `${seconds}s`, {size: 15, anchor: 'middle'});
  });
  body += line(pLeft, pTop, pLeft, pBottom, {stroke: COLORS.ink, sw: 2});
  body += line(pLeft, pBottom, pRight, pBottom, {stroke: COLORS.ink, sw: 2});
  data.forEach((row, i) => {
    const x = pLeft + (Math.log10(row.latency_ms) - minLog) / (maxLog - minLog) * (pRight - pLeft);
    const y = pTop + 35 + i * 67;
    body += `<circle cx="${x}" cy="${y}" r="9" fill="${i < 3 ? COLORS.purple : COLORS.gray}" stroke="#FFFFFF" stroke-width="2"/>`;
    const [first, second] = labelLines[row.model];
    body += text(pLeft - 15, y - 3, first, {size: 14, anchor: 'end', weight: i < 3 ? 700 : 400});
    body += text(pLeft - 15, y + 14, second, {size: 14, anchor: 'end', weight: i < 3 ? 700 : 400});
  });
  body += text(1110, 615, 'Mean remote API latency (log scale)', {size: 18, anchor: 'middle'});
  await saveFigure('fig_compiler_backbones', body, 1400, 680);
}
main().catch(error => { console.error(error); process.exit(1); });
