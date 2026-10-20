// Retry only failed startup reads, once. Never use this for mutations or AI calls.
export async function synchronizeStartupReads(
  reads: Array<() => Promise<unknown>>,
  isCurrent: () => boolean,
  pause: () => Promise<void> = () => new Promise(resolve => setTimeout(resolve, 1000)),
): Promise<number> {
  let pending = reads;
  for (let attempt = 0; attempt < 2 && isCurrent(); attempt += 1) {
    const results = await Promise.allSettled(pending.map(read =>
      Promise.resolve().then(() => isCurrent() ? read() : undefined),
    ));
    if (!isCurrent()) return 0;
    pending = pending.filter((_, index) => results[index].status === "rejected");
    if (!pending.length) return 0;
    if (attempt === 0) await pause();
  }
  return isCurrent() ? pending.length : 0;
}
