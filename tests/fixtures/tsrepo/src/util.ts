export function splitHeader(header: string): [string, string] {
  const idx = header.indexOf(" ");
  if (idx < 0) return [header, ""];
  return [header.slice(0, idx), header.slice(idx + 1)];
}
