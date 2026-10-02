const {COLORS, csv, text, rect, arrow, multiline, saveFigure} = require('./paper_plot_style');

async function main() {
  const inputs = csv('pipeline_inputs.csv');
  const stages = csv('pipeline_stages.csv');
  let body = '';
  const inputY = [115, 235, 355];
  inputs.forEach((input, i) => {
    body += rect(70, inputY[i], 225, 82, {fill: COLORS.light, stroke: COLORS.gray, rx: 10});
    body += multiline(182, inputY[i] + 31, input.label, {size: 19, maxChars: 20, weight: 700});
    body += text(182, inputY[i] + 68, input.detail, {size: 15, fill: COLORS.gray, anchor: 'middle', family: 'Arial, sans-serif'});
    body += arrow(300, inputY[i] + 41, 390, 260, {stroke: COLORS.gray, sw: 2});
  });
  const xs = [390, 635, 880, 1125];
  const fills = ['#DCEAF7', '#DFF2EF', '#FFF0D8', '#ECE5F5'];
  stages.forEach((stage, i) => {
    body += rect(xs[i], 185, 210, 155, {fill: fills[i], stroke: COLORS.ink, sw: 1.6, rx: 12});
    body += text(xs[i] + 105, 220, `Stage ${stage.stage}`, {size: 16, fill: COLORS.gray, anchor: 'middle', family: 'Arial, sans-serif'});
    body += multiline(xs[i] + 105, 260, stage.label, {size: 21, maxChars: 18, weight: 700});
    body += multiline(xs[i] + 105, 304, stage.detail, {size: 16, maxChars: 28, fill: COLORS.gray, family: 'Arial, sans-serif'});
    if (i < stages.length - 1) body += arrow(xs[i] + 214, 262, xs[i + 1] - 8, 262, {stroke: COLORS.ink, sw: 2.8});
  });
  body += rect(970, 455, 365, 88, {fill: '#F4F4F5', stroke: COLORS.gray, sw: 1.3, rx: 10});
  body += text(1152, 490, 'Structured answer', {size: 21, anchor: 'middle', weight: 700});
  body += text(1152, 520, 'value | accounting perspective | status | trace', {size: 16, anchor: 'middle', fill: COLORS.gray, family: 'Arial, sans-serif'});
  body += arrow(1230, 345, 1152, 449, {stroke: COLORS.ink, sw: 2.5});
  body += text(70, 600, 'Reproducibility layer: source identifiers, IFC STEP lines, release manifest, and SHA-256 checksums.', {size: 18, fill: COLORS.gray, italic: true});
  await saveFigure('fig_system_pipeline', body, 1400, 650);
}
main().catch(error => { console.error(error); process.exit(1); });
