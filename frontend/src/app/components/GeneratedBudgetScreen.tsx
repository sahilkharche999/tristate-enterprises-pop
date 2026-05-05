import { useState } from 'react';
import { Link, useNavigate } from 'react-router';
import { ArrowLeft, CheckCircle2, Download, Lock, Settings } from 'lucide-react';
import { toast } from 'sonner';

import { Button } from './ui/button';
import { downloadBudgetVersionFile } from '../api/budgetHistory';
import type { HOARecord } from '../api/hoa';
import { toNum, type SheetTable } from '../api/macros';
import type { LineItem } from '../data/mockData';
import { calcProposed, formatCurrency, formatTimestamp } from '../lib/budget';
import { getErrorMessage } from '../lib/errors';
import { formatFiscalYearLabel } from '../lib/hoa';

interface BudgetTotals {
  totalIncome: number;
  totalOperatingExpense: number;
  totalReserveContributions: number;
}

function parseBudgetPreview(budgetPreview: SheetTable): BudgetTotals {
  let totalIncome = 0;
  let totalOperatingExpense = 0;
  let totalReserveContributions = 0;

  for (const row of budgetPreview.rows) {
    const label = row.slice(0, 5).find((value) => value != null && value !== '');
    if (label == null) continue;
    const normalizedLabel = String(label).toLowerCase();

    // Find the largest absolute numeric value in the row — this is the annual
    // total regardless of which column layout is used (old 12-month or new 8-column).
    let best = 0;
    for (const cell of row) {
      const num = toNum(cell);
      if (Math.abs(num) > Math.abs(best)) best = num;
    }

    if (normalizedLabel.includes('total income')) totalIncome = best;
    else if (
      normalizedLabel.includes('total operating') ||
      normalizedLabel.includes('total expense - operating') ||
      normalizedLabel.includes('total expense')
    ) {
      totalOperatingExpense = best;
    } else if (
      normalizedLabel.includes('total reserve') ||
      normalizedLabel.includes('total expense - reserve') ||
      normalizedLabel.includes('total allocation')
    ) {
      totalReserveContributions = best;
    }
  }

  return { totalIncome, totalOperatingExpense, totalReserveContributions };
}

interface GeneratedBudgetScreenProps {
  hoa: HOARecord;
  hoaId: string;
  versionId: number;
  lineItems: LineItem[];
  versionCode: string;
  stage: string;
  label?: string | null;
  createdAt: string;
  sourceUploadFilename?: string;
  onRegenerateSnapshot: () => Promise<void> | void;
  onBackToDraft: () => Promise<void> | void;
  budgetPreview?: SheetTable | null;
  growthFactor?: number;
  growthFactorNote?: string;
  reserveInflationRate?: number;
  isRegenerating?: boolean;
  readOnly?: boolean;
}

function formatReserveInflation(rate?: number) {
  const normalizedRate = typeof rate === 'number' && Number.isFinite(rate) ? rate : 0;
  return `${(normalizedRate * 100).toFixed(1)}%`;
}

export function GeneratedBudgetScreen({
  hoa,
  hoaId,
  versionId,
  lineItems,
  versionCode,
  stage,
  label,
  createdAt,
  sourceUploadFilename,
  onRegenerateSnapshot,
  onBackToDraft,
  budgetPreview,
  growthFactor,
  growthFactorNote,
  reserveInflationRate,
  isRegenerating = false,
  readOnly = false,
}: GeneratedBudgetScreenProps) {
  const navigate = useNavigate();
  const [isDownloading, setIsDownloading] = useState(false);
  const fiscalYearLabel = formatFiscalYearLabel(
    hoa.fiscal_year_start_month,
    hoa.fiscal_year_end_month,
  );
  const isFinalVersion = stage === 'Final';

  let totalIncome: number;
  let totalOperatingExpense: number;
  let totalReserveContributions: number;

  if (budgetPreview) {
    ({ totalIncome, totalOperatingExpense, totalReserveContributions } = parseBudgetPreview(budgetPreview));
  } else {
    ({ totalIncome, totalOperatingExpense, totalReserveContributions } = lineItems.reduce(
      (accumulator, item) => {
        const proposed = calcProposed(item.annualBudget, item.percentChange);
        if (item.category === 'income') accumulator.totalIncome += proposed;
        else if (item.category === 'operating') accumulator.totalOperatingExpense += proposed;
        else if (item.category === 'reserve' || item.category === 'reserve_income' || item.category === 'reserve_expense') accumulator.totalReserveContributions += proposed;
        return accumulator;
      },
      { totalIncome: 0, totalOperatingExpense: 0, totalReserveContributions: 0 },
    ));
  }

  const totalExpense = totalOperatingExpense + totalReserveContributions;
  const netOperatingIncome = totalIncome - totalExpense;
  const monthlyNOI = netOperatingIncome / 12;
  const netMargin = totalIncome !== 0 ? (netOperatingIncome / totalIncome) * 100 : 0;

  const handleDownloadVersion = async () => {
    setIsDownloading(true);
    try {
      const blob = await downloadBudgetVersionFile(hoaId, versionId);
      const url = URL.createObjectURL(blob);
      const link = document.createElement('a');
      link.href = url;
      link.download = `${versionCode}-budget.xlsx`;
      link.click();
      URL.revokeObjectURL(url);
      toast.success(`${versionCode} downloaded.`);
    } catch (error) {
      toast.error(getErrorMessage(error, 'Failed to download the version workbook.'));
    } finally {
      setIsDownloading(false);
    }
  };

  return (
    <div className="min-h-screen bg-white">
      <header className="sticky top-0 z-10 border-b border-[#e5e5e5] bg-white shadow-sm">
        <div className="flex items-center justify-between px-8 py-6">
          <div className="flex items-center gap-6">
            <Button
              variant="ghost"
              size="icon"
              onClick={onBackToDraft}
              className="hover:bg-[#f5f5f5]"
            >
              <ArrowLeft className="h-5 w-5 text-[#525252]" />
            </Button>
            <div>
              <h1 className="text-xl font-semibold text-[#111111]">{hoa.name}</h1>
              <p className="text-sm text-[#737373]">Fiscal Year: {fiscalYearLabel}</p>
            </div>
          </div>
          <div className="flex items-center gap-4">
            <div className="text-right">
              <p className="text-xs text-[#a3a3a3]">{versionCode}</p>
              <p className="text-sm font-medium text-[#525252]">{formatTimestamp(new Date(createdAt))}</p>
            </div>
            <div className="text-right">
              <p className="text-xs text-[#a3a3a3]">Stage</p>
              <p className="text-sm font-medium text-[#525252]">
                {stage}
                {isFinalVersion ? ' • Final' : ''}
              </p>
            </div>
            {growthFactor != null && (
              <div className="text-right">
                <p className="text-xs text-[#a3a3a3]">Growth Factor</p>
                <p className="text-sm font-medium text-[#525252]">
                  {growthFactor.toFixed(4)}
                  {growthFactorNote ? (
                    <span className="ml-1 text-xs text-[#737373]">({growthFactorNote})</span>
                  ) : null}
                </p>
              </div>
            )}
            <div className="text-right">
              <p className="text-xs text-[#a3a3a3]">Reserve inflation</p>
              <p className="text-sm font-medium text-[#525252]">
                {formatReserveInflation(reserveInflationRate)}
              </p>
            </div>
            <div className="h-8 w-px bg-[#e5e5e5]"></div>
            <Link to={`/hoa/${hoaId}/settings`}>
              <Button variant="ghost" size="icon" className="hover:bg-[#f5f5f5]">
                <Settings className="h-5 w-5 text-[#525252]" />
              </Button>
            </Link>
          </div>
        </div>
      </header>

      <main className="mx-auto max-w-7xl px-8 py-8">
        <div className="mb-8 rounded-lg border border-[#e5e5e5] bg-[#fafafa] p-6">
          <div className="flex items-start gap-3">
            {readOnly ? (
              <Lock className="mt-0.5 h-5 w-5 flex-shrink-0 text-[#111111]" />
            ) : (
              <CheckCircle2 className="mt-0.5 h-5 w-5 flex-shrink-0 text-[#111111]" />
            )}
            <div>
              <h2 className="mb-1 text-lg font-semibold text-[#111111]">
                {readOnly ? 'Historical Version Review' : 'Budget Generated Successfully'}
              </h2>
              <p className="text-sm text-[#737373]">
                {readOnly
                  ? `You are reviewing ${versionCode} in read-only mode. Historical versions stay immutable.`
                  : `This snapshot was persisted as ${versionCode}. Regenerating creates a new immutable version.`}
              </p>
              {label ? <p className="mt-2 text-sm text-[#525252]">Label: {label}</p> : null}
              {sourceUploadFilename ? (
                <p className="mt-2 text-sm text-[#525252]">Source upload: {sourceUploadFilename}</p>
              ) : null}
              <p className="mt-2 text-sm text-[#525252]">
                Reserve inflation: {formatReserveInflation(reserveInflationRate)}
              </p>
            </div>
          </div>
        </div>

        <div className="mb-8 grid grid-cols-3 gap-6">
          <div className="rounded-lg border border-[#e5e5e5] bg-white p-6">
            <p className="mb-3 text-xs font-medium uppercase tracking-wide text-[#a3a3a3]">Total Income</p>
            <p className="text-2xl font-semibold text-[#111111]">{formatCurrency(totalIncome)}</p>
          </div>

          <div className="rounded-lg border border-[#e5e5e5] bg-white p-6">
            <p className="mb-3 text-xs font-medium uppercase tracking-wide text-[#a3a3a3]">Total Expense</p>
            <p className="text-2xl font-semibold text-[#111111]">{formatCurrency(totalExpense)}</p>
            <p className="mt-2 text-xs text-[#737373]">
              Operating: {formatCurrency(totalOperatingExpense)} • Reserve:{' '}
              {formatCurrency(totalReserveContributions)}
            </p>
          </div>

          <div className="rounded-lg border border-[#e5e5e5] bg-white p-6">
            <p className="mb-3 text-xs font-medium uppercase tracking-wide text-[#a3a3a3]">
              Net Operating Income
            </p>
            <p className="text-2xl font-semibold text-[#111111]">{formatCurrency(netOperatingIncome)}</p>
          </div>
        </div>

        <div className="mb-8 overflow-hidden rounded-lg border border-[#e5e5e5] bg-[#fafafa]">
          <div className="border-b border-[#e5e5e5] bg-white px-6 py-4">
            <h3 className="text-base font-semibold text-[#111111]">Version Review</h3>
          </div>

          <div className="p-6">
            <div className="grid grid-cols-3 gap-6">
              <div className="rounded-lg border border-[#e5e5e5] bg-white p-5">
                <p className="mb-3 text-sm font-semibold text-[#111111]">Net Check</p>
                <p className="mb-3 text-xs text-[#737373]">
                  Income – Expense matches Net Operating Income
                </p>
                <div className="rounded border border-[#e5e5e5] bg-[#fafafa] p-3 font-mono text-xs text-[#525252]">
                  {formatCurrency(totalIncome)} – {formatCurrency(totalExpense)} ={' '}
                  {formatCurrency(netOperatingIncome)}
                </div>
              </div>

              <div className="rounded-lg border border-[#e5e5e5] bg-white p-5">
                <p className="mb-3 text-sm font-semibold text-[#111111]">Monthly NOI</p>
                <p className="mb-3 text-xs text-[#737373]">Net Operating Income ÷ 12</p>
                <p className="text-xl font-semibold text-[#111111]">{formatCurrency(monthlyNOI)}</p>
              </div>

              <div className="rounded-lg border border-[#e5e5e5] bg-white p-5">
                <p className="mb-3 text-sm font-semibold text-[#111111]">Net Margin</p>
                <p className="mb-3 text-xs text-[#737373]">
                  (Net Operating Income ÷ Total Income) × 100
                </p>
                <p className="text-xl font-semibold text-[#111111]">{netMargin.toFixed(2)}%</p>
              </div>
            </div>
          </div>
        </div>

        <div className="mb-8 flex items-center justify-between border-b border-[#e5e5e5] pb-8">
          <Button
            variant="outline"
            onClick={onBackToDraft}
            className="border-[#e5e5e5] text-[#525252] hover:bg-[#f5f5f5] hover:text-[#111111]"
          >
            {readOnly ? 'Return to Budget Workspace' : 'Back to Draft'}
          </Button>
          <div className="flex items-center gap-3">
            <Button
              variant="outline"
              onClick={() => void handleDownloadVersion()}
              disabled={isDownloading}
              className="border-[#e5e5e5] text-[#525252] hover:bg-[#f5f5f5] hover:text-[#111111]"
            >
              <Download className="mr-2 h-4 w-4" />
              {isDownloading ? 'Downloading...' : 'Download Version'}
            </Button>
            {!readOnly && (
              <Button
                onClick={() => void onRegenerateSnapshot()}
                disabled={isRegenerating}
                className="bg-[#111111] text-white shadow-sm hover:bg-[#262626]"
              >
                {isRegenerating ? 'Generating...' : 'Create New Immutable Version'}
              </Button>
            )}
          </div>
        </div>

        {!readOnly && (
          <div className="flex justify-center">
            <Button
              variant="outline"
              onClick={() => navigate(`/hoa/${hoaId}?view=budget`)}
              className="border-[#e5e5e5] text-[#525252] hover:bg-[#f5f5f5] hover:text-[#111111]"
            >
              View Detailed Budget Table
            </Button>
          </div>
        )}
      </main>
    </div>
  );
}
