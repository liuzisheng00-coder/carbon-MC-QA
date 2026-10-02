const fs = require('fs');
const path = require('path');
const sharp = require('sharp');

const ROOT = __dirname;
const DATA_DIR = path.join(ROOT, 'data');
const W = 1400;
const H = 720;
const COLORS = {
  navy: '#1F4E79', blue: '#4E79A7', teal: '#2A9D8F', orange: '#E69F00',
  red: '#D55E00', purple: '#7B61A8', gray: '#6B7280', light: '#F7F8FA',
  grid: '#D1D5DB', ink: '#1F2937', white: '#FFFFFF'
};

function csv(file) {
  const lines = fs.readFileSync(path.join(DATA_DIR, file), 'utf8').trim().split(/\r?\n/);
  const keys = lines.shift().split(',');
  return lines.map(line => {
    const cells = line.split(',');
    return Object.fromEntries(keys.map((key, i) => [key, cells[i] ?? '']));
  });
}

function esc(value) {
  return String(value).replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;');
}

function text(x, y, value, options = {}) {
  const {size = 24, fill = COLORS.ink, anchor = 'start', weight = 400, family = 'Times New Roman, Times, serif', italic = false} = options;
  return `<text x="${x}" y="${y}" text-anchor="${anchor}" fill="${fill}" font-family="${family}" font-size="${size}" font-weight="${weight}"${italic ? ' font-style="italic"' : ''}>${esc(value)}</text>`;
}

function rect(x, y, width, height, options = {}) {
  const {fill = COLORS.white, stroke = COLORS.grid, sw = 1.5, rx = 10, opacity = 1} = options;
  return `<rect x="${x}" y="${y}" width="${width}" height="${height}" rx="${rx}" fill="${fill}" stroke="${stroke}" stroke-width="${sw}" opacity="${opacity}"/>`;
}

function line(x1, y1, x2, y2, options = {}) {
  const {stroke = COLORS.ink, sw = 2, dash = ''} = options;
  return `<line x1="${x1}" y1="${y1}" x2="${x2}" y2="${y2}" stroke="${stroke}" stroke-width="${sw}"${dash ? ` stroke-dasharray="${dash}"` : ''}/>`;
}

function arrow(x1, y1, x2, y2, options = {}) {
  const {stroke = COLORS.ink, sw = 2.5, marker = 'arrow'} = options;
  return `<line x1="${x1}" y1="${y1}" x2="${x2}" y2="${y2}" stroke="${stroke}" stroke-width="${sw}" marker-end="url(#${marker})"/>`;
}

function wrap(value, maxChars) {
  const words = String(value).split(' ');
  const result = [];
  let current = '';
  words.forEach(word => {
    const candidate = current ? `${current} ${word}` : word;
    if (candidate.length > maxChars && current) {
      result.push(current);
      current = word;
    } else current = candidate;
  });
  if (current) result.push(current);
  return result;
}

function multiline(x, y, value, options = {}) {
  const {size = 21, lineHeight = size * 1.25, maxChars = 20, anchor = 'middle', ...rest} = options;
  return wrap(value, maxChars).map((part, i) => text(x, y + i * lineHeight, part, {...rest, size, anchor})).join('');
}

function svg(body, width = W, height = H) {
  return `<?xml version="1.0" encoding="UTF-8"?>\n<svg xmlns="http://www.w3.org/2000/svg" width="${width}" height="${height}" viewBox="0 0 ${width} ${height}">\n<defs><marker id="arrow" markerWidth="10" markerHeight="10" refX="8" refY="3" orient="auto"><path d="M0,0 L0,6 L9,3 z" fill="${COLORS.ink}"/></marker></defs>\n<rect width="100%" height="100%" fill="#FFFFFF"/>\n${body}\n</svg>`;
}

async function saveFigure(name, body, width = W, height = H) {
  const source = svg(body, width, height);
  const svgPath = path.join(ROOT, `${name}.svg`);
  const pngPath = path.join(ROOT, `${name}.png`);
  fs.writeFileSync(svgPath, source, 'utf8');
  await sharp(Buffer.from(source), {density: 300}).png({compressionLevel: 9}).toFile(pngPath);
  console.log(`Saved ${path.basename(svgPath)} and ${path.basename(pngPath)}`);
}

module.exports = {COLORS, W, H, csv, text, rect, line, arrow, wrap, multiline, saveFigure};
