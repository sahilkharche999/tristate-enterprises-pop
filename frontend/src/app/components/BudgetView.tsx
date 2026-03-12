import { type LineItem } from '../data/mockData';
import { formatCurrency, getCategoryLabel, calcProposed, calcMonthly, calcCategoryTotal } from '../lib/budget';

interface BudgetViewProps {
  lineItems: LineItem[];
  units: number;
}

export function BudgetView({ lineItems, units }: BudgetViewProps) {
  const groupedItems = lineItems.reduce((acc, item) => {
    if (!acc[item.category]) {
      acc[item.category] = [];
    }
    acc[item.category].push(item);
    return acc;
  }, {} as Record<string, LineItem[]>);

  const totalOperating = calcCategoryTotal(groupedItems['operating'] || [], 'proposedChange');
  const totalReserve = calcCategoryTotal(groupedItems['reserve'] || [], 'proposedChange');
  // Total annual requirement = expenses only (operating + reserve), not income
  const totalAnnual = totalOperating + totalReserve;
  const monthlyPerUnit = totalAnnual / 12 / units;

  return (
    <div className="space-y-8">
      {/* Table */}
      <div className="bg-white border border-[#E5E5E5] rounded-lg overflow-hidden">
        <div className="overflow-x-auto">
          <table className="w-full">
            <thead className="bg-[#F7F7F7] border-b border-[#E5E5E5]">
              <tr>
                <th className="text-left px-6 py-4 text-sm font-medium text-[#111111]">Line Item</th>
                <th className="text-right px-6 py-4 text-sm font-medium text-[#111111]">YTD Actual</th>
                <th className="text-right px-6 py-4 text-sm font-medium text-[#111111]">Projection</th>
                <th className="text-right px-6 py-4 text-sm font-medium text-[#111111]">Final Approved</th>
                <th className="text-right px-6 py-4 text-sm font-medium text-[#111111]">Monthly</th>
              </tr>
            </thead>
            <tbody>
              {Object.entries(groupedItems).flatMap(([category, items]) => [
                // Category Header
                <tr key={`${category}-header`} className="bg-[#F7F7F7]">
                  <td colSpan={5} className="px-6 py-3 text-sm font-medium text-[#111111]">
                    {getCategoryLabel(category)}
                  </td>
                </tr>,
                // Line Items
                ...items.map((item) => {
                  if (item.readOnly) {
                    return (
                      <tr key={item.id} className="border-b border-[#E5E5E5] h-14 opacity-60">
                        <td className="px-6 py-4 text-sm text-[#525252] italic">{item.name}</td>
                        <td className="px-6 py-4 text-sm text-[#666666] text-right">{formatCurrency(item.ytdActual)}</td>
                        <td className="px-6 py-4 text-sm text-[#a3a3a3] text-right">—</td>
                        <td className="px-6 py-4 text-sm text-[#a3a3a3] text-right">—</td>
                        <td className="px-6 py-4 text-sm text-[#a3a3a3] text-right">—</td>
                      </tr>
                    );
                  }

                  const projection = item.projection ?? 0;
                  const proposedChange = calcProposed(item.annualBudget, item.percentChange);
                  const monthly = calcMonthly(proposedChange);

                  return (
                    <tr
                      key={item.id}
                      className="border-b border-[#E5E5E5] h-14"
                    >
                      <td className="px-6 py-4 text-sm text-[#111111]">{item.name}</td>
                      <td className="px-6 py-4 text-sm text-[#666666] text-right">
                        {formatCurrency(item.ytdActual)}
                      </td>
                      <td className="px-6 py-4 text-sm text-[#666666] text-right">
                        {formatCurrency(projection)}
                      </td>
                      <td className="px-6 py-4 text-sm font-medium text-[#111111] text-right">
                        {formatCurrency(proposedChange)}
                      </td>
                      <td className="px-6 py-4 text-sm text-[#666666] text-right">
                        {formatCurrency(monthly)}
                      </td>
                    </tr>
                  );
                }),
                // Category Total
                <tr key={`${category}-total`} className="bg-[#F7F7F7] font-medium border-b border-[#E5E5E5]">
                  <td className="px-6 py-3 text-sm text-[#111111]">{getCategoryLabel(category)} Total</td>
                  <td className="px-6 py-3 text-sm text-[#666666] text-right">
                    {formatCurrency(calcCategoryTotal(items, 'ytdActual'))}
                  </td>
                  <td className="px-6 py-3 text-sm text-[#666666] text-right">
                    {formatCurrency(calcCategoryTotal(items, 'projection'))}
                  </td>
                  <td className="px-6 py-3 text-sm font-medium text-[#111111] text-right">
                    {formatCurrency(calcCategoryTotal(items, 'proposedChange'))}
                  </td>
                  <td className="px-6 py-3 text-sm text-[#666666] text-right">
                    {formatCurrency(calcCategoryTotal(items, 'monthly'))}
                  </td>
                </tr>
              ])}
            </tbody>
          </table>
        </div>
      </div>

      {/* Budget Summary */}
      <div className="space-y-4">
        <h3 className="text-lg font-medium text-[#111111]">Budget Summary</h3>
        <div className="grid grid-cols-4 gap-6">
          <div className="bg-[#F7F7F7] border border-[#E5E5E5] rounded-lg p-6">
            <div className="text-sm text-[#666666] mb-2">Total Operating</div>
            <div className="text-2xl font-medium text-[#111111]">{formatCurrency(totalOperating)}</div>
          </div>
          <div className="bg-[#F7F7F7] border border-[#E5E5E5] rounded-lg p-6">
            <div className="text-sm text-[#666666] mb-2">Total Reserve</div>
            <div className="text-2xl font-medium text-[#111111]">{formatCurrency(totalReserve)}</div>
          </div>
          <div className="bg-[#F7F7F7] border border-[#E5E5E5] rounded-lg p-6">
            <div className="text-sm text-[#666666] mb-2">Total Annual Requirement</div>
            <div className="text-2xl font-medium text-[#111111]">{formatCurrency(totalAnnual)}</div>
          </div>
          <div className="bg-[#F7F7F7] border border-[#E5E5E5] rounded-lg p-6">
            <div className="text-sm text-[#666666] mb-2">Monthly Per Unit</div>
            <div className="text-2xl font-medium text-[#111111]">{formatCurrency(monthlyPerUnit)}</div>
          </div>
        </div>
      </div>
    </div>
  );
}
