// Disclosure facts read from uploaded budget files into HOA settings: the
// upload toast and the provenance hints in Settings → Disclosure.

/** Upload toast for the HOA-settings facts a file carried; null when there is nothing to review. */
export function disclosureFactsSummary(facts: Array<{ status: string }> = []): string | null {
  const filled = facts.filter((fact) => fact.status === 'applied').length;
  const suggested = facts.filter((fact) => fact.status === 'suggested').length;
  const parts = [
    filled ? `Filled ${filled} HOA setting${filled === 1 ? '' : 's'} from this file` : '',
    suggested
      ? `${suggested} suggestion${suggested === 1 ? '' : 's'} to review in Settings → Disclosure`
      : '',
  ].filter(Boolean);
  return parts.length ? `${parts.join(' · ')}.` : null;
}

/** Whether a setting already holds a fact's value: numbers exactly, text ignoring
 * case and spacing, and a structured value by the parts the file printed. */
export function sameFactValue(current: unknown, found: unknown): boolean {
  if (Array.isArray(found)) return JSON.stringify(current) === JSON.stringify(found);
  if (found && typeof found === 'object') {
    return Object.entries(found).every(
      ([key, value]) =>
        value == null || sameFactValue((current as Record<string, unknown> | null)?.[key], value),
    );
  }
  if (typeof found === 'number') return current === found;
  const text = (value: unknown) => String(value ?? '').trim().replace(/\s+/g, ' ').toLowerCase();
  return text(current) === text(found);
}
