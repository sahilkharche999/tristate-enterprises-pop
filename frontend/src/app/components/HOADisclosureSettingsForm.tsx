import { useEffect, useState } from 'react';
import {
  type AssessmentIncreaseBracket,
  type BoardDeferralEntry,
  type HOADisclosureSettings,
  type OutstandingLoan,
  type ReserveFundingSource,
  type SpecialAssessmentEntry,
  getHOADisclosureSettings,
  putHOADisclosureSettings,
} from '../api/hoaSettings';
import { Button } from './ui/button';

const EMPTY_ASSESSMENT: SpecialAssessmentEntry = {
  due_date: '',
  amount_per_unit: 0,
  frequency: 'month',
  purpose: '',
};

const EMPTY_LOAN: OutstandingLoan = {
  balance: 0,
  lender: '',
  original_amount: null,
  interest_rate: null,
  payoff_date: null,
  purpose: null,
};

function parseList(raw: string | null | undefined): SpecialAssessmentEntry[] {
  if (!raw) return [];
  try {
    const parsed = JSON.parse(raw);
    return Array.isArray(parsed) ? parsed : [];
  } catch {
    return [];
  }
}

function parseAssessmentSchedule(raw: string | null | undefined): AssessmentIncreaseBracket[] {
  if (!raw) return [];
  try {
    const parsed = JSON.parse(raw);
    return Array.isArray(parsed) ? parsed : [];
  } catch {
    return [];
  }
}

function parseDeferrals(raw: string | null | undefined): BoardDeferralEntry[] {
  if (!raw) return [];
  try {
    const parsed = JSON.parse(raw);
    return Array.isArray(parsed) ? parsed : [];
  } catch {
    return [];
  }
}

function parseLoan(raw: string | null | undefined): OutstandingLoan | null {
  if (!raw) return null;
  try {
    const parsed = JSON.parse(raw);
    return parsed && typeof parsed === 'object' ? (parsed as OutstandingLoan) : null;
  } catch {
    return null;
  }
}

export function HOADisclosureSettingsForm({ hoaId }: { hoaId: number }) {
  const [settings, setSettings] = useState<HOADisclosureSettings | null>(null);
  const [saving, setSaving] = useState(false);
  const [savedAt, setSavedAt] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    void getHOADisclosureSettings(hoaId)
      .then(setSettings)
      .catch((e) => setError(e instanceof Error ? e.message : String(e)));
  }, [hoaId]);

  if (error) return <p className="text-xs text-[#b91c1c]">{error}</p>;
  if (!settings) return <p className="text-sm text-[#737373]">Loading…</p>;

  const update = <K extends keyof HOADisclosureSettings>(k: K, v: HOADisclosureSettings[K]) =>
    setSettings((prev) => (prev ? { ...prev, [k]: v } : prev));

  const updateList = (
    key: 'special_assessments_json' | 'additional_assessments_needed_json',
    next: SpecialAssessmentEntry[],
  ) => update(key, JSON.stringify(next));

  const save = async () => {
    if (!settings) return;
    setSaving(true);
    setError(null);
    try {
      const { property_id: _propertyId, ...writable } = settings;
      const next = await putHOADisclosureSettings(hoaId, writable);
      setSettings(next);
      setSavedAt(new Date().toLocaleTimeString());
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setSaving(false);
    }
  };

  const scalarFields: Array<[keyof HOADisclosureSettings, string, 'text' | 'number']> = [
    ['management_company', 'Management company name', 'text'],
    ['management_company_address', 'Management company address', 'text'],
    ['management_company_phone', 'Phone', 'text'],
    ['management_company_fax', 'Fax', 'text'],
    ['management_company_web', 'Website', 'text'],
    ['cpa_firm_name', 'CPA firm name', 'text'],
    ['cpa_firm_address', 'CPA firm address', 'text'],
    ['reserve_study_expert_name', 'Reserve study expert', 'text'],
    ['reserve_study_date', 'Reserve study report date (free text, e.g. "November 5, 2024")', 'text'],
    ['letter_signed_by', 'Letter signed by', 'text'],
    ['reserve_cash_balance_eoy_prior', 'Reserve cash balance (end of prior year)', 'number'],
    ['fund_balance_boy_operations', 'Operating fund balance (beginning of year)', 'number'],
    ['monthly_assessment_per_unit_prior', 'Monthly assessment per unit (prior year)', 'number'],
    ['interest_rate_after_tax', 'Interest rate after tax (decimal, e.g. 0.018)', 'number'],
    ['replacement_fund_monthly_assessment_per_unit', 'Replacement fund monthly assessment per unit (30-year forecast base; blank = derive from reserve study ÷ units ÷ 12)', 'number'],
    ['approved_monthly_assessment_per_unit', 'Approved monthly assessment per unit (overrides derived; cents preserved)', 'number'],
    ['income_tax_provision_override', 'Income tax provision override (annual; blank = derive from interest revenue)', 'number'],
    ['reserve_funding_manual_amount', 'Reserve funding manual amount (annual; used when source = manual)', 'number'],
    ['letter_date', 'Cover-letter date (free text, e.g. "November 18, 2025")', 'text'],
    ['accountant_report_date', 'Accountants’ compilation report date (free text)', 'text'],
    ['reserve_funding_plan_date', 'Reserve funding plan date (free text)', 'text'],
  ];
  // Removed (drifting-puzzling-grove refactor) — these never change
  // year-over-year, so they live on the Property row (state, entity_type,
  // incorporation_year), are app-wide constants (letter_signed_by_title),
  // or are industry-standard defaults seeded once on DB init
  // (replacement_cost_increase_rate). Surfacing them per-package adds noise.

  const specialAssessments = parseList(settings.special_assessments_json);
  const additionalAssessments = parseList(settings.additional_assessments_needed_json);
  const loan = parseLoan(settings.outstanding_loan_json);

  const renderAssessmentEditor = (
    title: string,
    helpText: string,
    rows: SpecialAssessmentEntry[],
    key: 'special_assessments_json' | 'additional_assessments_needed_json',
    includePurpose: boolean,
  ) => (
    <div className="space-y-2 border border-[#e5e5e5] rounded p-3">
      <div className="flex items-center justify-between">
        <div>
          <h4 className="text-sm font-semibold text-[#111]">{title}</h4>
          <p className="text-xs text-[#737373]">{helpText}</p>
        </div>
        <Button
          variant="outline"
          onClick={() => updateList(key, [...rows, { ...EMPTY_ASSESSMENT }])}
          className="text-xs"
        >
          + Add row
        </Button>
      </div>
      {rows.length === 0 ? (
        <p className="text-xs text-[#a3a3a3] italic">
          No rows — PDF will render &ldquo;None scheduled&rdquo;.
        </p>
      ) : (
        rows.map((row, i) => (
          <div key={i} className="space-y-1 border-l-2 border-[#e5e5e5] pl-2">
            <div
              className={`grid gap-2 ${includePurpose ? 'sm:grid-cols-[1fr_1fr_1fr_2fr_auto]' : 'sm:grid-cols-[1fr_1fr_1fr_auto]'}`}
            >
              <input
                placeholder="Due date"
                value={row.due_date}
                onChange={(e) => {
                  const next = [...rows];
                  next[i] = { ...row, due_date: e.target.value };
                  updateList(key, next);
                }}
                className="border border-[#d4d4d4] rounded px-2 py-1 text-sm"
              />
              <input
                type="number"
                step="0.01"
                placeholder="Amount per unit"
                value={row.amount_per_unit ?? ''}
                onChange={(e) => {
                  const next = [...rows];
                  next[i] = { ...row, amount_per_unit: Number(e.target.value) };
                  updateList(key, next);
                }}
                className="border border-[#d4d4d4] rounded px-2 py-1 text-sm"
              />
              <input
                placeholder="month / year / one-time"
                value={row.frequency}
                onChange={(e) => {
                  const next = [...rows];
                  next[i] = { ...row, frequency: e.target.value };
                  updateList(key, next);
                }}
                className="border border-[#d4d4d4] rounded px-2 py-1 text-sm"
              />
              {includePurpose ? (
                <input
                  placeholder="Purpose"
                  value={row.purpose}
                  onChange={(e) => {
                    const next = [...rows];
                    next[i] = { ...row, purpose: e.target.value };
                    updateList(key, next);
                  }}
                  className="border border-[#d4d4d4] rounded px-2 py-1 text-sm"
                />
              ) : null}
              <Button
                variant="ghost"
                onClick={() => updateList(key, rows.filter((_, j) => j !== i))}
                className="text-xs text-[#b91c1c]"
              >
                Remove
              </Button>
            </div>
            {/* Phase 4.4 status + display-language fields per dre-driven-assessment-engine.
                Status drives the cover-letter §5300 + §5570 wording branches. */}
            <div className="grid gap-2 sm:grid-cols-[1fr_1fr_2fr_auto]">
              <select
                value={row.status || 'approved_scheduled'}
                onChange={(e) => {
                  const next = [...rows];
                  next[i] = { ...row, status: e.target.value as 'none' | 'approved_scheduled' | 'possible_disclosure_only' };
                  updateList(key, next);
                }}
                className="border border-[#d4d4d4] rounded px-2 py-1 text-xs"
              >
                <option value="approved_scheduled">Approved + scheduled</option>
                <option value="possible_disclosure_only">Possible (disclosure only)</option>
                <option value="none">None</option>
              </select>
              <label className="flex items-center gap-1 text-xs">
                <input
                  type="checkbox"
                  checked={row.included_in_regular_monthly || false}
                  onChange={(e) => {
                    const next = [...rows];
                    next[i] = { ...row, included_in_regular_monthly: e.target.checked };
                    updateList(key, next);
                  }}
                />
                Included in monthly
              </label>
              <input
                placeholder="Disclosure language (for 'possible' status)"
                value={row.display_language || ''}
                onChange={(e) => {
                  const next = [...rows];
                  next[i] = { ...row, display_language: e.target.value };
                  updateList(key, next);
                }}
                className="border border-[#d4d4d4] rounded px-2 py-1 text-xs"
              />
              <input
                placeholder="Label (e.g. Pool deck repair)"
                value={row.label || ''}
                onChange={(e) => {
                  const next = [...rows];
                  next[i] = { ...row, label: e.target.value };
                  updateList(key, next);
                }}
                className="border border-[#d4d4d4] rounded px-2 py-1 text-xs"
              />
            </div>
          </div>
        ))
      )}
    </div>
  );

  const renderLoanEditor = () => (
    <div className="space-y-2 border border-[#e5e5e5] rounded p-3">
      <div className="flex items-center justify-between">
        <div>
          <h4 className="text-sm font-semibold text-[#111]">Outstanding loan</h4>
          <p className="text-xs text-[#737373]">
            When blank, Note 8 renders &ldquo;no outstanding loans&rdquo;. Fill in to disclose an active loan.
          </p>
        </div>
        {loan ? (
          <Button
            variant="ghost"
            onClick={() => update('outstanding_loan_json', null)}
            className="text-xs text-[#b91c1c]"
          >
            Remove loan
          </Button>
        ) : (
          <Button
            variant="outline"
            onClick={() => update('outstanding_loan_json', JSON.stringify({ ...EMPTY_LOAN }))}
            className="text-xs"
          >
            + Add loan
          </Button>
        )}
      </div>
      {loan ? (
        <div className="grid grid-cols-1 gap-2 sm:grid-cols-2">
          {([
            ['balance', 'Outstanding balance', 'number'],
            ['lender', 'Lender', 'text'],
            ['original_amount', 'Original principal', 'number'],
            ['interest_rate', 'Interest rate (decimal, e.g. 0.045)', 'number'],
            ['payoff_date', 'Scheduled payoff date', 'text'],
            ['purpose', 'Purpose', 'text'],
          ] as Array<[keyof OutstandingLoan, string, 'number' | 'text']>).map(([k, label, type]) => (
            <label key={k} className="block text-sm">
              <span className="block text-xs text-[#737373] mb-1">{label}</span>
              <input
                type={type}
                step={type === 'number' ? 'any' : undefined}
                value={loan[k] === null || loan[k] === undefined ? '' : String(loan[k])}
                onChange={(e) => {
                  const next: OutstandingLoan = {
                    ...loan,
                    [k]: type === 'number'
                      ? (e.target.value === '' ? null : Number(e.target.value))
                      : (e.target.value === '' ? null : e.target.value),
                  };
                  update('outstanding_loan_json', JSON.stringify(next));
                }}
                className="w-full border border-[#d4d4d4] rounded px-2 py-1 text-sm"
              />
            </label>
          ))}
        </div>
      ) : null}
    </div>
  );

  return (
    <div className="space-y-4">
      <p className="text-xs text-[#737373]">
        These values drive the rendered disclosure package PDF — every field below is read at
        generate time. Blank numeric fields mean &ldquo;derive from data&rdquo; (e.g. blank
        income-tax override → tax = interest revenue × 30%).
      </p>

      <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
        {scalarFields.map(([key, label, type]) => (
          <label key={key} className="block text-sm">
            <span className="block text-xs text-[#737373] mb-1">{label}</span>
            <input
              type={type}
              step={type === 'number' ? 'any' : undefined}
              value={settings[key] === null || settings[key] === undefined ? '' : String(settings[key])}
              onChange={(e) => {
                if (type === 'number') {
                  // Empty string is a meaningful "unset" for overrideable
                  // numeric fields (approved_monthly_assessment_per_unit,
                  // income_tax_provision_override, reserve_funding_manual_amount).
                  // For required fields (rates, cash balance) treat as 0.
                  const v = e.target.value === ''
                    ? (key === 'approved_monthly_assessment_per_unit'
                       || key === 'income_tax_provision_override'
                       || key === 'reserve_funding_manual_amount'
                       || key === 'replacement_fund_monthly_assessment_per_unit'
                        ? null : 0)
                    : Number(e.target.value);
                  update(key, v as never);
                } else {
                  update(key, e.target.value as never);
                }
              }}
              className="w-full border border-[#d4d4d4] rounded px-2 py-1 text-sm"
            />
          </label>
        ))}

        <label className="block text-sm">
          <span className="block text-xs text-[#737373] mb-1">
            Reserve funding source (which value drives Note 6 monthly funding)
          </span>
          <select
            value={settings.reserve_funding_source}
            onChange={(e) =>
              update('reserve_funding_source', e.target.value as ReserveFundingSource)
            }
            className="w-full border border-[#d4d4d4] rounded px-2 py-1 text-sm"
          >
            <option value="reserve_study_provision">
              Reserve study annual provision ÷ 12 (default)
            </option>
            <option value="budget_allocation_line">
              Budget &ldquo;Reserve - Allocation/Transfer&rdquo; line ÷ 12
            </option>
            <option value="manual">
              Manual override (use field above)
            </option>
          </select>
        </label>
      </div>

      {renderAssessmentEditor(
        'Special assessments scheduled (§5570 item 2)',
        'Already-approved special assessments. Each row prints in the §5570 disclosure summary; cents preserved.',
        specialAssessments,
        'special_assessments_json',
        true,
      )}

      {renderAssessmentEditor(
        'Additional assessments needed (§5570 item 4)',
        'Future assessments needed to keep reserves sufficient. No "purpose" column on this form.',
        additionalAssessments,
        'additional_assessments_needed_json',
        false,
      )}

      {renderLoanEditor()}

      {/* 30-year reserve funding study — assessment escalation schedule */}
      {(() => {
        const schedule = parseAssessmentSchedule(settings.assessment_increase_schedule_json);
        const update_schedule = (next: AssessmentIncreaseBracket[]) =>
          update('assessment_increase_schedule_json', JSON.stringify(next));
        return (
          <div className="space-y-2 border border-[#e5e5e5] rounded p-3">
            <div className="flex items-center justify-between">
              <div>
                <h4 className="text-sm font-semibold text-[#111]">
                  Assessment escalation schedule (30-year forecast)
                </h4>
                <p className="text-xs text-[#737373]">
                  Year-bracket rates that escalate the replacement-fund monthly
                  assessment in the 30-year cash-flow forecast. Rate is decimal
                  (0.03 = 3%). Old Mill default: 3% through 2035, 3% through 2045, 0% thru 2055.
                </p>
              </div>
              <Button
                variant="outline"
                onClick={() =>
                  update_schedule([
                    ...schedule,
                    { start_year: 2026, end_year: 2035, rate: 0.03 },
                  ])
                }
                className="text-xs"
              >
                + Add bracket
              </Button>
            </div>
            {schedule.length === 0 ? (
              <p className="text-xs text-[#a3a3a3] italic">
                No brackets — assessment will not escalate over the 30-year window.
              </p>
            ) : (
              schedule.map((row, i) => (
                <div key={i} className="grid gap-2 sm:grid-cols-[1fr_1fr_1fr_auto]">
                  <input
                    type="number"
                    placeholder="Start year"
                    value={row.start_year ?? ''}
                    onChange={(e) => {
                      const next = [...schedule];
                      next[i] = { ...row, start_year: Number(e.target.value) };
                      update_schedule(next);
                    }}
                    className="border border-[#d4d4d4] rounded px-2 py-1 text-sm"
                  />
                  <input
                    type="number"
                    placeholder="End year"
                    value={row.end_year ?? ''}
                    onChange={(e) => {
                      const next = [...schedule];
                      next[i] = { ...row, end_year: Number(e.target.value) };
                      update_schedule(next);
                    }}
                    className="border border-[#d4d4d4] rounded px-2 py-1 text-sm"
                  />
                  <input
                    type="number"
                    step="0.001"
                    placeholder="Rate (decimal, e.g. 0.03)"
                    value={row.rate ?? ''}
                    onChange={(e) => {
                      const next = [...schedule];
                      next[i] = { ...row, rate: Number(e.target.value) };
                      update_schedule(next);
                    }}
                    className="border border-[#d4d4d4] rounded px-2 py-1 text-sm"
                  />
                  <Button
                    variant="ghost"
                    onClick={() => update_schedule(schedule.filter((_, j) => j !== i))}
                    className="text-xs text-[#b91c1c]"
                  >
                    Remove
                  </Button>
                </div>
              ))
            )}
          </div>
        );
      })()}

      {/* 30-year reserve funding study — board-approved deferrals */}
      {(() => {
        const deferrals = parseDeferrals(settings.board_deferrals_json);
        const update_deferrals = (next: BoardDeferralEntry[]) =>
          update('board_deferrals_json', JSON.stringify(next));
        return (
          <div className="space-y-2 border border-[#e5e5e5] rounded p-3">
            <div className="flex items-center justify-between">
              <div>
                <h4 className="text-sm font-semibold text-[#111]">
                  Board-approved deferrals (30-year forecast)
                </h4>
                <p className="text-xs text-[#737373]">
                  Year-by-year reductions to scheduled reserve expenditures
                  approved by the board. Usually empty.
                </p>
              </div>
              <Button
                variant="outline"
                onClick={() =>
                  update_deferrals([...deferrals, { year: 2026, amount: 0 }])
                }
                className="text-xs"
              >
                + Add deferral
              </Button>
            </div>
            {deferrals.length === 0 ? (
              <p className="text-xs text-[#a3a3a3] italic">
                No deferrals — full scheduled expenditures will flow into the forecast.
              </p>
            ) : (
              deferrals.map((row, i) => (
                <div key={i} className="grid gap-2 sm:grid-cols-[1fr_1fr_auto]">
                  <input
                    type="number"
                    placeholder="Year"
                    value={row.year ?? ''}
                    onChange={(e) => {
                      const next = [...deferrals];
                      next[i] = { ...row, year: Number(e.target.value) };
                      update_deferrals(next);
                    }}
                    className="border border-[#d4d4d4] rounded px-2 py-1 text-sm"
                  />
                  <input
                    type="number"
                    step="0.01"
                    placeholder="Amount ($)"
                    value={row.amount ?? ''}
                    onChange={(e) => {
                      const next = [...deferrals];
                      next[i] = { ...row, amount: Number(e.target.value) };
                      update_deferrals(next);
                    }}
                    className="border border-[#d4d4d4] rounded px-2 py-1 text-sm"
                  />
                  <Button
                    variant="ghost"
                    onClick={() => update_deferrals(deferrals.filter((_, j) => j !== i))}
                    className="text-xs text-[#b91c1c]"
                  >
                    Remove
                  </Button>
                </div>
              ))
            )}
          </div>
        );
      })()}

      <div className="flex items-center gap-3">
        <Button onClick={save} disabled={saving} className="bg-[#111] text-white hover:bg-[#262626]">
          {saving ? 'Saving…' : 'Save changes'}
        </Button>
        {savedAt ? <span className="text-xs text-[#737373]">Saved at {savedAt}</span> : null}
      </div>
    </div>
  );
}
