const {COLORS, csv, text, line, rect, multiline, saveFigure} = require('./paper_plot_style');

async function main() {
  const data = csv('baseline_comparison.csv').map(row => ({...row, strict_accuracy: +row.strict_accuracy, any_number_hit_rate: +row.any_number_hit_rate, strict_sd: +row.strict_sd || 0}));
  const left = 275, right = 1320, top = 70, bottom = 565, height = bottom - top;
  const xStep = (right - left) / data.length;
  const barWidth = 55;
  let body = '';
  for (let tick = 0; tick <= 100; tick += 20) {
    const y = bottom - tick / 100 * height;
    body += line(left, y, right, y, {stroke: tick === 0 ? COLORS.ink : COLORS.grid, sw: tick === 0 ? 2 : 1, dash: tick === 0 ? '' : '5 5'});
    body += text(left - 16, y + 6, `${tick}`, {size: 18, anchor: 'end'});
  }
  body += text(55, 315, 'Accuracy (%)', {size: 20, anchor: 'middle'}).replace('<text ', '<text transform="rotate(-90 55 315)" ');
  data.forEach((row, i) => {
    const cx = left + xStep * (i + 0.5);
    const values = [row.strict_accuracy, row.any_number_hit_rate];
    const fills = [COLORS.blue, COLORS.orange];
    values.forEach((value, j) => {
      const x = cx + (j === 0 ? -barWidth - 5 : 5);
      const y = bottom - value / 100 * height;
      body += rect(x, y, barWidth, bottom - y, {fill: fills[j], stroke: fills[j], rx: 2});
      body += text(x + barWidth / 2, y - 10, value.toFixed(1), {size: 18, anchor: 'middle', weight: 700});
      if (j === 0 && row.strict_sd) {
        const err = row.strict_sd / 100 * height;
        body += line(x + barWidth / 2, y - err, x + barWidth / 2, y + err, {stroke: COLORS.ink, sw: 1.6});
        body += line(x + barWidth / 2 - 7, y - err, x + barWidth / 2 + 7, y - err, {stroke: COLORS.ink, sw: 1.6});
        body += line(x + barWidth / 2 - 7, y + err, x + barWidth / 2 + 7, y + err, {stroke: COLORS.ink, sw: 1.6});
      }
    });
    body += multiline(cx, 605, row.system, {size: 17, maxChars: 20, weight: i === data.length - 1 ? 700 : 400});
  });
  body += rect(275, 20, 22, 16, {fill: COLORS.blue, stroke: COLORS.blue, rx: 1});
  body += text(308, 35, 'Strict accuracy', {size: 17});
  body += rect(470, 20, 22, 16, {fill: COLORS.orange, stroke: COLORS.orange, rx: 1});
  body += text(503, 35, 'Any-number hit rate', {size: 17});
  body += text(1295, 35, 'Error bar: SD across 3 runs (full system)', {size: 15, anchor: 'end', fill: COLORS.gray, italic: true});
  await saveFigure('fig_baseline_comparison', body, 1400, 690);
}
main().catch(error => { console.error(error); process.exit(1); });
