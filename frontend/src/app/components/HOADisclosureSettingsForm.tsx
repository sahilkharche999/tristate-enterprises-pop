import { useEffect, useState } from 'react';
import {
  type HOADisclosureSettings,
  getHOADisclosureSettings,
  putHOADisclosureSettings,
} from '../api/hoaSettings';
import { Button } from './ui/button';

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

  const save = async () => {
    if (!settings) return;
    setSaving(true);
    setError(null);
    try {
      // Strip read-only fields the backend allow-list rejects.
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

  const fields: Array<[keyof HOADisclosureSettings, string, 'text' | 'number']> = [
    ['management_company', 'Management company name', 'text'],
    ['management_company_address', 'Management company address', 'text'],
    ['management_company_phone', 'Phone', 'text'],
    ['management_company_fax', 'Fax', 'text'],
    ['management_company_web', 'Website', 'text'],
    ['cpa_firm_name', 'CPA firm name', 'text'],
    ['cpa_firm_address', 'CPA firm address', 'text'],
    ['reserve_study_expert_name', 'Reserve study expert', 'text'],
    ['letter_signed_by', 'Letter signed by', 'text'],
    ['reserve_cash_balance_eoy_prior', 'Reserve cash balance (end of prior year)', 'number'],
    ['fund_balance_boy_operations', 'Operating fund balance (beginning of year)', 'number'],
    ['monthly_assessment_per_unit_prior', 'Monthly assessment per unit (prior year)', 'number'],
    ['interest_rate_after_tax', 'Interest rate after tax (decimal, e.g. 0.018)', 'number'],
    ['replacement_cost_increase_rate', 'Replacement cost increase rate (decimal, e.g. 0.03)', 'number'],
  ];

  return (
    <div className="space-y-4">
      <p className="text-xs text-[#737373]">
        These values drive the rendered disclosure package PDF — every field below is read at
        generate time, no hardcoded defaults remain in the templates.
      </p>
      <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
        {fields.map(([key, label, type]) => (
          <label key={key} className="block text-sm">
            <span className="block text-xs text-[#737373] mb-1">{label}</span>
            <input
              type={type}
              step={type === 'number' ? 'any' : undefined}
              value={settings[key] === null || settings[key] === undefined ? '' : String(settings[key])}
              onChange={(e) =>
                update(
                  key,
                  (type === 'number' ? Number(e.target.value) : e.target.value) as never,
                )
              }
              className="w-full border border-[#d4d4d4] rounded px-2 py-1 text-sm"
            />
          </label>
        ))}
      </div>
      <div className="flex items-center gap-3">
        <Button onClick={save} disabled={saving} className="bg-[#111] text-white hover:bg-[#262626]">
          {saving ? 'Saving…' : 'Save changes'}
        </Button>
        {savedAt ? <span className="text-xs text-[#737373]">Saved at {savedAt}</span> : null}
      </div>
    </div>
  );
}
