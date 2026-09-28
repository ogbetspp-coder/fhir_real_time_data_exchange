// CRC-32C (Castagnoli), the checksum Cloud KMS uses to prove a digest and a signature were not
// corrupted between the client and the HSM. Unsigned 32-bit result. Bitwise rather than by
// table: it only ever sees a 32-byte digest and a 256-byte signature.
const POLYNOMIAL = 0x82f63b78;

export function crc32c(bytes: Uint8Array): number {
  let crc = 0xffffffff;
  for (const byte of bytes) {
    crc ^= byte;
    for (let bit = 0; bit < 8; bit += 1) {
      crc = crc & 1 ? (crc >>> 1) ^ POLYNOMIAL : crc >>> 1;
    }
  }
  return (crc ^ 0xffffffff) >>> 0;
}
