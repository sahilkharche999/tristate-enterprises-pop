import { Link, useParams, useNavigate, useSearchParams } from 'react-router';
import { ArrowLeft, CheckCircle2, Settings } from 'lucide-react';
import { Button } from './ui/button';
import { hoaList, type LineItem } from '../data/mockData';

interface GeneratedBudgetScreenProps {
  lineItems: LineItem[];
  version: number;
  generatedAt: Date;
  onRegenerateSnapshot: () => void;
}

export function GeneratedBudgetScreen({
  lineItems,
  version,
  generatedAt,
  onRegenerateSnapshot,
}: GeneratedBudgetScreenProps) {
  const { id } = useParams<{ id: string }>();
  const navigate = useNavigate();
  const [searchParams, setSearchParams] = useSearchParams();
  const hoa = hoaList.find((h) => h.id === id);

  const formatCurrency = (value: number) => {
    return new Intl.NumberFormat('en-US', {
      style: 'currency',
      currency: 'USD',
      minimumFractionDigits: 2,
      maximumFractionDigits: 2,
    }).format(value);
  };

  const formatTimestamp = (date: Date) => {
    return date.toLocaleString('en-US', {
      month: 'short',
      day: 'numeric',
      year: 'numeric',
      hour: '2-digit',
      minute: '2-digit',
    });
  };

  const calculateProjection = (ytd: number) => {
    // Extrapolate YTD to full year (assuming 8 months of data)
    return (ytd / 8) * 12;
  };

  const calculateProposedChange = (projection: number, percentChange: number) => {
    return projection * (1 + percentChange / 100);
  };

  // Calculate totals
  const incomeItems = lineItems.filter((item) => item.category === 'income');
  const operatingItems = lineItems.filter((item) => item.category === 'operating');
  const reserveItems = lineItems.filter((item) => item.category === 'reserve');

  const totalIncome = incomeItems.reduce((sum, item) => {
    const projection = calculateProjection(item.ytdActual);
    return sum + calculateProposedChange(projection, item.percentChange);
  }, 0);

  const totalOperatingExpense = operatingItems.reduce((sum, item) => {
    const projection = calculateProjection(item.ytdActual);
    return sum + calculateProposedChange(projection, item.percentChange);
  }, 0);

  const totalReserveContributions = reserveItems.reduce((sum, item) => {
    const projection = calculateProjection(item.ytdActual);
    return sum + calculateProposedChange(projection, item.percentChange);
  }, 0);

  const totalExpense = totalOperatingExpense + totalReserveContributions;
  const netOperatingIncome = totalIncome - totalExpense;
  const monthlyNOI = netOperatingIncome / 12;
  const netMargin = totalIncome !== 0 ? (netOperatingIncome / totalIncome) * 100 : 0;

  if (!hoa) {
    return (
      <div className="min-h-screen bg-white flex items-center justify-center">
        <p className="text-[#666666]">HOA not found</p>
      </div>
    );
  }

  return (
    <div className="min-h-screen bg-white">
      {/* Sticky Header */}
      <header className="border-b border-[#e5e5e5] bg-white sticky top-0 z-10 shadow-sm">
        <div className="px-8 py-6 flex items-center justify-between">
          <div className="flex items-center gap-6">
            <Button
              variant="ghost"
              size="icon"
              onClick={() => navigate(`/hoa/${id}`)}
              className="hover:bg-[#f5f5f5]"
            >
              <ArrowLeft className="w-5 h-5 text-[#525252]" />
            </Button>
            <div>
              <h1 className="text-xl font-semibold text-[#111111]">{hoa.name}</h1>
              <p className="text-sm text-[#737373]">Fiscal Year: {hoa.fiscalYear}</p>
            </div>
          </div>
          <div className="flex items-center gap-4">
            <div className="text-right">
              <p className="text-xs text-[#a3a3a3]">Draft Version {version}</p>
              <p className="text-sm text-[#525252] font-medium">{formatTimestamp(generatedAt)}</p>
            </div>
            <div className="h-8 w-px bg-[#e5e5e5]"></div>
            <Link to={`/hoa/${id}/settings`}>
              <Button variant="ghost" size="icon" className="hover:bg-[#f5f5f5]">
                <Settings className="w-5 h-5 text-[#525252]" />
              </Button>
            </Link>
          </div>
        </div>
      </header>

      {/* Main Content */}
      <main className="px-8 py-8 max-w-7xl mx-auto">
        {/* Success Message */}
        <div className="mb-8 bg-[#fafafa] border border-[#e5e5e5] rounded-lg p-6">
          <div className="flex items-start gap-3">
            <CheckCircle2 className="w-5 h-5 text-[#111111] flex-shrink-0 mt-0.5" />
            <div>
              <h2 className="text-lg font-semibold text-[#111111] mb-1">Budget Generated Successfully</h2>
              <p className="text-sm text-[#737373]">
                Your budget projections have been locked as Draft Version {version}. All calculations are based on
                current enriched table data.
              </p>
            </div>
          </div>
        </div>

        {/* Summary Cards */}
        <div className="grid grid-cols-3 gap-6 mb-8">
          {/* Total Income */}
          <div className="bg-white border border-[#e5e5e5] rounded-lg p-6">
            <p className="text-xs font-medium text-[#a3a3a3] uppercase tracking-wide mb-3">
              Total Income
            </p>
            <p className="text-2xl font-semibold text-[#111111]">{formatCurrency(totalIncome)}</p>
          </div>

          {/* Total Expense */}
          <div className="bg-white border border-[#e5e5e5] rounded-lg p-6">
            <p className="text-xs font-medium text-[#a3a3a3] uppercase tracking-wide mb-3">
              Total Expense
            </p>
            <p className="text-2xl font-semibold text-[#111111]">{formatCurrency(totalExpense)}</p>
            <p className="text-xs text-[#737373] mt-2">
              Operating: {formatCurrency(totalOperatingExpense)} • Reserve: {formatCurrency(totalReserveContributions)}
            </p>
          </div>

          {/* Net Operating Income */}
          <div className="bg-white border border-[#e5e5e5] rounded-lg p-6">
            <p className="text-xs font-medium text-[#a3a3a3] uppercase tracking-wide mb-3">
              Net Operating Income
            </p>
            <p className={`text-2xl font-semibold ${netOperatingIncome >= 0 ? 'text-[#111111]' : 'text-[#111111]'}`}>
              {formatCurrency(netOperatingIncome)}
            </p>
          </div>
        </div>

        {/* Budget Review Section */}
        <div className="bg-[#fafafa] border border-[#e5e5e5] rounded-lg overflow-hidden mb-8">
          <div className="px-6 py-4 bg-white border-b border-[#e5e5e5]">
            <h3 className="text-base font-semibold text-[#111111]">Budget Review</h3>
          </div>

          <div className="p-6">
            <div className="grid grid-cols-3 gap-6">
              {/* Net Check Card */}
              <div className="bg-white border border-[#e5e5e5] rounded-lg p-5">
                <p className="text-sm font-semibold text-[#111111] mb-3">Net Check</p>
                <p className="text-xs text-[#737373] mb-3">
                  Income – Expense matches Net Operating Income
                </p>
                <div className="bg-[#fafafa] border border-[#e5e5e5] rounded p-3 text-xs text-[#525252] font-mono">
                  {formatCurrency(totalIncome)} – {formatCurrency(totalExpense)} = {formatCurrency(netOperatingIncome)}
                </div>
              </div>

              {/* Monthly NOI Card */}
              <div className="bg-white border border-[#e5e5e5] rounded-lg p-5">
                <p className="text-sm font-semibold text-[#111111] mb-3">Monthly NOI</p>
                <p className="text-xs text-[#737373] mb-3">
                  Net Operating Income ÷ 12
                </p>
                <p className="text-xl font-semibold text-[#111111]">
                  {formatCurrency(monthlyNOI)}
                </p>
              </div>

              {/* Net Margin Card */}
              <div className="bg-white border border-[#e5e5e5] rounded-lg p-5">
                <p className="text-sm font-semibold text-[#111111] mb-3">Net Margin</p>
                <p className="text-xs text-[#737373] mb-3">
                  (Net Operating Income ÷ Total Income) × 100
                </p>
                <p className="text-xl font-semibold text-[#111111]">
                  {netMargin.toFixed(2)}%
                </p>
              </div>
            </div>
          </div>
        </div>

        {/* Action Buttons */}
        <div className="flex items-center justify-between mb-8 pb-8 border-b border-[#e5e5e5]">
          <Button
            variant="outline"
            onClick={() => navigate(`/hoa/${id}`)}
            className="border-[#e5e5e5] text-[#525252] hover:bg-[#f5f5f5] hover:text-[#111111]"
          >
            Back to Enriched View
          </Button>
          <Button
            onClick={onRegenerateSnapshot}
            className="bg-[#111111] text-white hover:bg-[#262626] shadow-sm"
          >
            Regenerate Snapshot
          </Button>
        </div>

        {/* View Detailed Budget Table */}
        <div className="flex justify-center">
          <Button
            variant="outline"
            onClick={() => {
              setSearchParams({});
              navigate(`/hoa/${id}?view=budget`);
            }}
            className="border-[#e5e5e5] text-[#525252] hover:bg-[#f5f5f5] hover:text-[#111111]"
          >
            View Detailed Budget Table
          </Button>
        </div>
      </main>
    </div>
  );
}