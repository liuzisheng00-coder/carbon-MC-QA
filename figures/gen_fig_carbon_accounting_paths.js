const {COLORS, csv, text, rect, arrow, multiline, saveFigure} = require('./paper_plot_style');

async function main() {
  const rows = csv('carbon_accounting_paths.csv');
  const palette = [COLORS.blue, COLORS.teal, COLORS.orange];
  const yPositions = [105, 300, 495];
  let body = '';
  rows.forEach((row, rowIndex) => {
    const y = yPositions[rowIndex];
    const color = palette[rowIndex];
    const nodes = [row.node_1, row.node_2, row.node_3, row.node_4, row.node_5].filter(Boolean);
    const startX = 230;
    const step = 930 / (nodes.length - 1);
    body += rect(60, y - 36, 135, 66, {fill: color, stroke: color, rx: 8});
    body += multiline(127, y - 6, row.perspective, {size: 19, maxChars: 13, fill: COLORS.white, weight: 700});
    nodes.forEach((node, i) => {
      const x = startX + i * step;
      body += rect(x, y - 52, 160, 86, {fill: '#FFFFFF', stroke: color, sw: 2, rx: 10});
      body += multiline(x + 80, y - 12, node, {size: 18, maxChars: 18, weight: 600});
      if (i < nodes.length - 1) body += arrow(x + 164, y - 9, x + step - 8, y - 9, {stroke: color, sw: 2.5});
    });
    body += text(230, y + 70, row.annotation, {size: 18, fill: COLORS.gray, italic: true});
  });
  await saveFigure('fig_carbon_accounting_paths', body, 1400, 660);
}
main().catch(error => { console.error(error); process.exit(1); });
