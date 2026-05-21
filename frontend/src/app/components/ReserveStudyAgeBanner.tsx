// Stale reserve study banner (Phase 5.7 task 175 of dre-driven-assessment-engine).
//
// CA §5550 requires reserve studies to be refreshed every 3 years.
// This banner mirrors the preflight check `check_reserve_study_age`
// on the backend so operators see a heads-up before they try to
// finalize a disclosure package.
//
// Severities map to the same `severity` field the backend emits:
//   - blocking: study older than 3 years (refresh required)
//   - warning:  study exactly 3 years old (refresh next year)
//   - info:     study 2 years old (refresh due soon)
//   - none:     study < 2 years old (no banner)

import { useEffect, useState } from 'react';

type Props = {
  fiscalYear: number;
  studyDate: string | null | undefined;
};

type Severity = 'blocking' | 'warning' | 'info' | 'none';

function parseStudyYear(studyDate: string | null | undefined): number | null {
  if (!studyDate) return null;
  // Accept ISO ('2025-09-01'), month-name ('September 2025'), short month
  // ('Sep 2025'), and US-style date strings — we just need the year.
  const m = String(studyDate).match(/(19|20)\d{2}/);
  return m ? parseInt(m[0], 10) : null;
}

function ageSeverity(fiscalYear: number, studyYear: number | null): Severity {
  if (studyYear == null) return 'none';
  const age = fiscalYear - studyYear;
  if (age >= 4) return 'blocking';
  if (age === 3) return 'warning';
  if (age === 2) return 'info';
  return 'none';
}

const SEVERITY_STYLE: Record<Severity, { bg: string; text: string; label: string }> = {
  blocking: {
    bg: 'bg-red-50 border-red-300',
    text: 'text-red-800',
    label: 'BLOCKING',
  },
  warning: {
    bg: 'bg-yellow-50 border-yellow-300',
    text: 'text-yellow-800',
    label: 'WARNING',
  },
  info: {
    bg: 'bg-blue-50 border-blue-300',
    text: 'text-blue-800',
    label: 'INFO',
  },
  none: { bg: '', text: '', label: '' },
};

export function ReserveStudyAgeBanner({ fiscalYear, studyDate }: Props) {
  const [dismissed, setDismissed] = useState(false);
  useEffect(() => {
    setDismissed(false);
  }, [fiscalYear, studyDate]);

  const studyYear = parseStudyYear(studyDate);
  const severity = ageSeverity(fiscalYear, studyYear);

  if (severity === 'none' || dismissed) return null;

  const style = SEVERITY_STYLE[severity];
  const age = fiscalYear - (studyYear ?? fiscalYear);

  const message =
    severity === 'blocking'
      ? `Reserve study is ${age} years old (CA §5550 requires refresh within 3 years). Package generation will block until a new study is uploaded.`
      : severity === 'warning'
        ? `Reserve study is exactly 3 years old. Refresh is required before the next fiscal year.`
        : `Reserve study is 2 years old. Plan a refresh before next fiscal year to stay within the §5550 3-year window.`;

  return (
    <div
      className={`rounded border ${style.bg} ${style.text} flex items-start justify-between p-3 text-sm`}
    >
      <div>
        <span className="mr-2 rounded bg-white/50 px-1 py-0.5 text-xs font-bold">
          {style.label}
        </span>
        <span>{message}</span>
        {studyYear && (
          <span className="ml-1 text-xs opacity-75">
            (study dated {studyDate}, fiscal year {fiscalYear})
          </span>
        )}
      </div>
      <button
        type="button"
        onClick={() => setDismissed(true)}
        className="ml-3 rounded border border-current px-2 py-0.5 text-xs hover:bg-white/30"
      >
        Dismiss
      </button>
    </div>
  );
}
