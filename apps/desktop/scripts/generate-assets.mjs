import { mkdir, writeFile } from "node:fs/promises";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { deflateSync } from "node:zlib";

const packageRoot = path.resolve(
  path.dirname(fileURLToPath(import.meta.url)),
  ".."
);
const outputDirectory = path.join(packageRoot, ".runtime", "build-assets");
const png = createIconPng(256);
const ico = createIco(png);

await mkdir(outputDirectory, { recursive: true });
await Promise.all([
  writeFile(path.join(outputDirectory, "icon.png"), png),
  writeFile(path.join(outputDirectory, "icon.ico"), ico)
]);

function createIconPng(size) {
  const stride = size * 4;
  const scanlines = Buffer.alloc((stride + 1) * size);

  for (let y = 0; y < size; y += 1) {
    const row = y * (stride + 1);
    for (let x = 0; x < size; x += 1) {
      const offset = row + 1 + x * 4;
      const dx = (x - size / 2) / (size / 2);
      const dy = (y - size / 2) / (size / 2);
      const radius = Math.sqrt(dx * dx + dy * dy);
      const glow = Math.max(0, 1 - radius);
      const filmBand = x > 42 && x < 214 && y > 67 && y < 189;
      const aperture =
        Math.abs(radius - 0.48) < 0.1 &&
        !(
          x > size * 0.5 &&
          y > size * 0.32 &&
          y < size * 0.68
        );

      scanlines[offset] = Math.round(18 + glow * 30);
      scanlines[offset + 1] = Math.round(22 + glow * 36);
      scanlines[offset + 2] = Math.round(32 + glow * 48);
      scanlines[offset + 3] = 255;

      if (filmBand && (y < 82 || y > 174)) {
        scanlines[offset] = 229;
        scanlines[offset + 1] = 173;
        scanlines[offset + 2] = 87;
      }
      if (aperture) {
        scanlines[offset] = 244;
        scanlines[offset + 1] = 204;
        scanlines[offset + 2] = 121;
      }
    }
  }

  return Buffer.concat([
    pngSignature(),
    pngChunk("IHDR", createIhdr(size, size)),
    pngChunk("IDAT", deflateSync(scanlines, { level: 9 })),
    pngChunk("IEND", Buffer.alloc(0))
  ]);
}

function pngSignature() {
  return Buffer.from([137, 80, 78, 71, 13, 10, 26, 10]);
}

function createIhdr(width, height) {
  const data = Buffer.alloc(13);
  data.writeUInt32BE(width, 0);
  data.writeUInt32BE(height, 4);
  data[8] = 8;
  data[9] = 6;
  return data;
}

function pngChunk(type, data) {
  const typeBuffer = Buffer.from(type, "ascii");
  const chunk = Buffer.alloc(12 + data.length);
  chunk.writeUInt32BE(data.length, 0);
  typeBuffer.copy(chunk, 4);
  data.copy(chunk, 8);
  chunk.writeUInt32BE(crc32(Buffer.concat([typeBuffer, data])), 8 + data.length);
  return chunk;
}

function createIco(pngBuffer) {
  const header = Buffer.alloc(22);
  header.writeUInt16LE(0, 0);
  header.writeUInt16LE(1, 2);
  header.writeUInt16LE(1, 4);
  header[6] = 0;
  header[7] = 0;
  header[8] = 0;
  header[9] = 0;
  header.writeUInt16LE(1, 10);
  header.writeUInt16LE(32, 12);
  header.writeUInt32LE(pngBuffer.length, 14);
  header.writeUInt32LE(22, 18);
  return Buffer.concat([header, pngBuffer]);
}

function crc32(buffer) {
  let crc = 0xffffffff;
  for (const byte of buffer) {
    crc ^= byte;
    for (let bit = 0; bit < 8; bit += 1) {
      crc = (crc >>> 1) ^ (0xedb88320 & -(crc & 1));
    }
  }
  return (crc ^ 0xffffffff) >>> 0;
}
