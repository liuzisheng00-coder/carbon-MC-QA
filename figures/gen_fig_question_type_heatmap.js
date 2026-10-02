const {COLORS, csv, text, line, rect, saveFigure} = require('./paper_plot_style');

function color(value) {
  const t = Math.max(0, Math.min(1, (value - 35) / 65));
  const r = Math.round(245 - 196 * t);
  const g = Math.round(245 - 113 * t);
  const b = Math.round(245 - 118 * t);
  return `rgb(${r},${g},${b})`;
}

async function main() {
  const data = csv('question_type_performance.csv');
  const models = ['deepseek-chat', 'deepseek-v4-pro', 'qwen3.7-plus', 'qwen3.7-flash'];
  const labels = {'deepseek-chat': 'deepseek\nchat', 'deepseek-v4-pro': 'deepseek\nv4-pro', 'qwen3.7-plus': 'qwen3.7\nplus', 'qwen3.7-flash': 'qwen3.7\nflash'};
  const left = 310, top = 118, cw = 205, ch = 48;
  let body = '';
  models.forEach((model, c) => {
    const x = left + c * cw + cw / 2;
    const [first, second] = labels[model].split('\n');
    body += text(x, 62, first, {size: 20, anchor: 'middle', weight: 700});
    body += text(x, 88, second, {size: 20, anchor: 'middle', weight: 700});
  });
  data.forEach((row, r) => {
    const y = top + r * ch;
    if (r === 4) body += line(85, y - 9, 1200, y - 9, {stroke: COLORS.ink, sw: 2});
    body += text(280, y + 32, row.tag, {size: 19, anchor: 'end', weight: row.tag === 'Rank' ? 700 : 400});
    models.forEach((model, c) => {
      const value = +row[model];
      const x = left + c * cw;
      body += rect(x, y, cw - 5, ch - 5, {fill: color(value), stroke: '#FFFFFF', sw: 1, rx: 2});
      body += text(x + (cw - 5) / 2, y + 31, value.toFixed(1), {size: 18, anchor: 'middle', weight: 700, fill: value > 75 ? '#FFFFFF' : COLORS.ink, family: 'Arial, sans-serif'});
    });
  });
  body += text(95, 212, 'Perspective', {size: 18, anchor: 'middle', weight: 700}).replace('<text ', '<text transform="rotate(-90 95 212)" ');
  body += text(95, 508, 'Operation', {size: 18, anchor: 'middle', weight: 700}).replace('<text ', '<text transform="rotate(-90 95 508)" ');
  for (let i = 0; i < 130; i++) {
    const value = 35 + i / 129 * 65;
    body += rect(1245, 130 + i * 3, 25, 3.5, {fill: color(value), stroke: color(value), sw: 0, rx: 0});
  }
  body += text(1285, 140, '100', {size: 16});
  body += text(1285, 515, '35', {size: 16});
  body += text(1260, 555, 'Pass rate (%)', {size: 16, anchor: 'middle'});
  await saveFigure('fig_question_type_heatmap', body, 1400, 660);
}
main().catch(error => { console.error(error); process.exit(1); });
