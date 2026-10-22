// Draws the app icon (assets/icon.png, 1024x1024) with no dependencies: a dark rounded
// square with four candlesticks and a rising line. An original drawing, no brand marks.
// electron-builder turns the PNG into .icns. Rerun only to change the drawing.
const fs = require("node:fs");
const path = require("node:path");
const zlib = require("node:zlib");

const SIZE = 1024;
const SAMPLES = 3; // per axis, for smooth edges

// macOS icon grid: an 824 px body centred in the 1024 px canvas.
const BODY = { x: 100, y: 100, size: 824, radius: 185 };
const GREEN = [38, 194, 129];
const RED = [239, 83, 80];
const LINE = [120, 180, 255];
// [centre x, body top, body bottom, wick top, wick bottom, colour]
const CANDLES = [
  [300, 560, 700, 500, 760, RED],
  [430, 480, 620, 430, 680, GREEN],
  [560, 520, 600, 470, 650, RED],
  [690, 360, 540, 300, 600, GREEN],
];
const CANDLE_HALF = 42;
const WICK_HALF = 9;
const POLYLINE = [
  [220, 640],
  [380, 560],
  [520, 590],
  [800, 300],
];
const LINE_HALF = 16;

function insideRoundedSquare(x, y, { x: left, y: top, size, radius }) {
  const right = left + size;
  const bottom = top + size;
  if (x < left || x > right || y < top || y > bottom) return false;
  const cx = Math.min(Math.max(x, left + radius), right - radius);
  const cy = Math.min(Math.max(y, top + radius), bottom - radius);
  return (x - cx) ** 2 + (y - cy) ** 2 <= radius ** 2;
}

function distanceToSegment(px, py, [ax, ay], [bx, by]) {
  const dx = bx - ax;
  const dy = by - ay;
  const t = Math.max(
    0,
    Math.min(1, ((px - ax) * dx + (py - ay) * dy) / (dx * dx + dy * dy)),
  );
  return Math.hypot(px - (ax + t * dx), py - (ay + t * dy));
}

/** Colour at one sample point, or null outside the icon body. */
function sample(x, y) {
  if (!insideRoundedSquare(x, y, BODY)) return null;
  for (let i = 0; i + 1 < POLYLINE.length; i += 1) {
    if (distanceToSegment(x, y, POLYLINE[i], POLYLINE[i + 1]) <= LINE_HALF)
      return LINE;
  }
  for (const [cx, top, bottom, wickTop, wickBottom, colour] of CANDLES) {
    const body = Math.abs(x - cx) <= CANDLE_HALF && y >= top && y <= bottom;
    const wick =
      Math.abs(x - cx) <= WICK_HALF && y >= wickTop && y <= wickBottom;
    if (body || wick) return colour;
  }
  // Background: navy, slightly lighter at the top.
  const t = (y - BODY.y) / BODY.size;
  return [
    Math.round(34 - 18 * t),
    Math.round(44 - 23 * t),
    Math.round(66 - 34 * t),
  ];
}

function render() {
  const rowBytes = SIZE * 4 + 1;
  const raw = Buffer.alloc(rowBytes * SIZE);
  const n = SAMPLES * SAMPLES;
  for (let py = 0; py < SIZE; py += 1) {
    raw[py * rowBytes] = 0; // filter: none
    for (let px = 0; px < SIZE; px += 1) {
      let r = 0;
      let g = 0;
      let b = 0;
      let covered = 0;
      for (let sy = 0; sy < SAMPLES; sy += 1) {
        for (let sx = 0; sx < SAMPLES; sx += 1) {
          const colour = sample(
            px + (sx + 0.5) / SAMPLES,
            py + (sy + 0.5) / SAMPLES,
          );
          if (!colour) continue;
          r += colour[0];
          g += colour[1];
          b += colour[2];
          covered += 1;
        }
      }
      const at = py * rowBytes + 1 + px * 4;
      if (covered) {
        raw[at] = Math.round(r / covered);
        raw[at + 1] = Math.round(g / covered);
        raw[at + 2] = Math.round(b / covered);
      }
      raw[at + 3] = Math.round((255 * covered) / n);
    }
  }
  return raw;
}

const CRC_TABLE = Array.from({ length: 256 }, (_, n) => {
  let c = n;
  for (let k = 0; k < 8; k += 1) c = c & 1 ? 0xedb88320 ^ (c >>> 1) : c >>> 1;
  return c >>> 0;
});
function crc32(buffer) {
  let c = 0xffffffff;
  for (const byte of buffer) c = CRC_TABLE[(c ^ byte) & 0xff] ^ (c >>> 8);
  return (c ^ 0xffffffff) >>> 0;
}
function chunk(type, data) {
  const length = Buffer.alloc(4);
  length.writeUInt32BE(data.length);
  const body = Buffer.concat([Buffer.from(type, "ascii"), data]);
  const crc = Buffer.alloc(4);
  crc.writeUInt32BE(crc32(body));
  return Buffer.concat([length, body, crc]);
}

const header = Buffer.alloc(13);
header.writeUInt32BE(SIZE, 0);
header.writeUInt32BE(SIZE, 4);
header[8] = 8; // bit depth
header[9] = 6; // RGBA
const png = Buffer.concat([
  Buffer.from([0x89, 0x50, 0x4e, 0x47, 0x0d, 0x0a, 0x1a, 0x0a]),
  chunk("IHDR", header),
  chunk("IDAT", zlib.deflateSync(render(), { level: 9 })),
  chunk("IEND", Buffer.alloc(0)),
]);
const target = path.resolve(__dirname, "../assets/icon.png");
fs.mkdirSync(path.dirname(target), { recursive: true });
fs.writeFileSync(target, png);
console.log(`wrote ${target} (${png.length} bytes)`);
