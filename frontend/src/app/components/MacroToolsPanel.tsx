import { useState, useRef } from 'react';
import { Button } from './ui/button';
import { Input } from './ui/input';
import { Label } from './ui/label';
import * as api from '../api/macros';
import type { TableResponse, SumResponse, DupsResponse, SimpleResponse, RunPipelineResponse, CumulativeSumResponse } from '../api/macros';

// ─── Types ────────────────────────────────────────────────────────────────────

type ToolResult =
  | { kind: 'table'; data: TableResponse }
  | { kind: 'sum'; data: SumResponse }
  | { kind: 'dups'; data: DupsResponse }
  | { kind: 'simple'; data: SimpleResponse }
  | { kind: 'pipeline'; data: RunPipelineResponse }
  | { kind: 'cumsum'; data: CumulativeSumResponse }
  | { kind: 'download' };

// ─── Shared sub-components ────────────────────────────────────────────────────

function FileField({ fileRef }: { fileRef: React.RefObject<HTMLInputElement | null> }) {
  return (
    <div className="space-y-1">
      <Label className="text-xs text-[#525252]">Excel File (.xlsx / .xls)</Label>
      <Input ref={fileRef} type="file" accept=".xlsx,.xls" className="bg-white border-[#E5E5E5] text-sm" />
    </div>
  );
}

function InlineTable({ data }: { data: TableResponse }) {
  if (!data.rows.length) return <p className="text-xs text-[#737373]">No rows returned.</p>;
  return (
    <div className="overflow-x-auto border border-[#E5E5E5] rounded-lg mt-3">
      <table className="w-full text-xs">
        <thead className="bg-[#F7F7F7]">
          <tr>
            {data.headers.map((h, i) => (
              <th key={i} className="px-3 py-2 text-left font-medium text-[#111111] border-b border-[#E5E5E5]">
                {h ?? ''}
              </th>
            ))}
          </tr>
        </thead>
        <tbody className="bg-white">
          {data.rows.map((row, ri) => (
            <tr key={ri} className="border-b border-[#F0F0F0]">
              {row.map((cell, ci) => (
                <td key={ci} className="px-3 py-2 text-[#525252]">
                  {cell != null ? String(cell) : ''}
                </td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function ResultDisplay({ result }: { result: ToolResult }) {
  if (result.kind === 'download') return <p className="text-xs text-green-600 mt-2">File downloaded successfully.</p>;
  if (result.kind === 'table') return <InlineTable data={result.data} />;
  if (result.kind === 'pipeline') {
    const { data } = result;
    if (data.payment_search && typeof data.payment_search === 'object') {
      return <InlineTable data={data.payment_search as TableResponse} />;
    }
    return (
      <pre className="text-xs text-[#525252] bg-[#F7F7F7] border border-[#E5E5E5] rounded p-3 mt-2 overflow-x-auto">
        {JSON.stringify(data, null, 2)}
      </pre>
    );
  }
  if (result.kind === 'sum') {
    return (
      <p className="text-sm text-[#111111] mt-2">
        Sum: <strong>{result.data.sum}</strong> written to <code>{result.data.sum_cell}</code>
      </p>
    );
  }
  if (result.kind === 'cumsum') {
    return (
      <p className="text-sm text-[#111111] mt-2">
        Cumulative sum: <strong>{result.data.cumulative_sum}</strong> (rows {result.data.start_row}–{result.data.end_row})
      </p>
    );
  }
  if (result.kind === 'dups') {
    if (!result.data.duplicates.length) return <p className="text-xs text-[#737373] mt-2">No duplicates found.</p>;
    return (
      <div className="mt-2 space-y-1">
        <p className="text-xs font-medium text-[#111111]">{result.data.duplicates.length} duplicate(s) found:</p>
        {result.data.duplicates.map((d, i) => (
          <p key={i} className="text-xs text-[#525252]">Row {d.row}: {String(d.value)}</p>
        ))}
      </div>
    );
  }
  if (result.kind === 'simple') {
    return <p className="text-sm text-[#111111] mt-2">{result.data.message}</p>;
  }
  return null;
}

// ─── Individual Tool Cards ─────────────────────────────────────────────────────

function ToolCard({
  title,
  description,
  onRun,
  children,
}: {
  title: string;
  description: string;
  onRun: () => Promise<ToolResult | void>;
  children: React.ReactNode;
}) {
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [result, setResult] = useState<ToolResult | null>(null);

  const handleRun = async () => {
    setLoading(true);
    setError(null);
    setResult(null);
    try {
      const res = await onRun();
      if (res) setResult(res);
    } catch (err: unknown) {
      const apiErr = err as { message?: string };
      setError(apiErr?.message || 'An error occurred. Please try again.');
    } finally {
      setLoading(false);
    }
  };

  return (
    <div className="bg-white border border-[#E5E5E5] rounded-lg p-5 space-y-3">
      <div>
        <h4 className="text-sm font-semibold text-[#111111]">{title}</h4>
        <p className="text-xs text-[#737373] mt-0.5">{description}</p>
      </div>
      <div className="space-y-2">{children}</div>
      <Button
        size="sm"
        disabled={loading}
        onClick={handleRun}
        className="bg-[#111111] text-white hover:bg-[#262626] text-xs"
      >
        {loading ? 'Running...' : 'Run'}
      </Button>
      {error && <p className="text-xs text-red-500">{error}</p>}
      {result && <ResultDisplay result={result} />}
    </div>
  );
}

// ─── MacroToolsPanel ──────────────────────────────────────────────────────────

export function MacroToolsPanel() {
  // Remove Protection
  const removeProtFile = useRef<HTMLInputElement>(null);

  // Run Pipeline
  const pipelineFile = useRef<HTMLInputElement>(null);
  const [pipelineSheet, setPipelineSheet] = useState('Income Statement');

  // Payment Search
  const paymentFile = useRef<HTMLInputElement>(null);
  const [paymentSheet, setPaymentSheet] = useState('Sheet1');

  // Sum & Format
  const sumFile = useRef<HTMLInputElement>(null);
  const [sumSheet, setSumSheet] = useState('');
  const [sumStartCell, setSumStartCell] = useState('');
  const [sumRowCount, setSumRowCount] = useState('');
  const [sumTargetCol, setSumTargetCol] = useState('H');

  // ACH Sum
  const achFile = useRef<HTMLInputElement>(null);
  const [achSheet, setAchSheet] = useState('');
  const [achStartCell, setAchStartCell] = useState('');
  const [achFindText, setAchFindText] = useState('ACH Draft');

  // One Cell
  const oneCellFile = useRef<HTMLInputElement>(null);
  const [oneCellSheet, setOneCellSheet] = useState('');
  const [oneCellStartCell, setOneCellStartCell] = useState('');

  // Find Dups
  const dupsFile = useRef<HTMLInputElement>(null);
  const [dupsSheet, setDupsSheet] = useState('');
  const [dupsLookupCol, setDupsLookupCol] = useState('F');

  // Cumulative Sum
  const cumsumFile = useRef<HTMLInputElement>(null);
  const [cumsumSheet, setCumsumSheet] = useState('');
  const [cumsumStartRow, setCumsumStartRow] = useState('');
  const [cumsumStartCol, setCumsumStartCol] = useState('');
  const [cumsumEndRow, setCumsumEndRow] = useState('');

  const getFile = (ref: React.RefObject<HTMLInputElement | null>): File => {
    const file = ref.current?.files?.[0];
    if (!file) throw { message: 'Please select a file first.' };
    return file;
  };

  return (
    <div className="space-y-4">
      <h3 className="text-lg font-medium text-[#111111]">Macro Tools</h3>
      <p className="text-sm text-[#666666]">
        Run backend macro operations directly on Excel files. Each tool processes the uploaded file and returns results inline.
      </p>

      <div className="grid grid-cols-1 gap-4">
        {/* Remove Protection */}
        <ToolCard
          title="Remove Protection"
          description="Remove worksheet and workbook protection from an Excel file and download the unlocked version."
          onRun={async () => {
            const file = getFile(removeProtFile);
            const blob = await api.removeProtection({ file });
            const url = URL.createObjectURL(blob);
            const a = document.createElement('a');
            a.href = url;
            a.download = file.name.replace(/\.xlsx?$/, '') + '_unprotected.xlsx';
            a.click();
            URL.revokeObjectURL(url);
            return { kind: 'download' } as ToolResult;
          }}
        >
          <FileField fileRef={removeProtFile} />
        </ToolCard>

        {/* Run Pipeline */}
        <ToolCard
          title="Run Pipeline"
          description="Run the macro pipeline on an Income Statement sheet and view the combined result."
          onRun={async () => {
            const file = getFile(pipelineFile);
            const data = await api.runPipeline({ file, sheet: pipelineSheet || undefined });
            return { kind: 'pipeline', data } as ToolResult;
          }}
        >
          <FileField fileRef={pipelineFile} />
          <div className="space-y-1">
            <Label className="text-xs text-[#525252]">Sheet Name</Label>
            <Input value={pipelineSheet} onChange={(e) => setPipelineSheet(e.target.value)} className="bg-white border-[#E5E5E5] text-sm" placeholder="Income Statement" />
          </div>
        </ToolCard>

        {/* Payment Search */}
        <ToolCard
          title="Payment Search"
          description="Sort rows by vendor and date, format date column. Returns sorted table."
          onRun={async () => {
            const file = getFile(paymentFile);
            const data = await api.paymentSearch({ file, sheet: paymentSheet || undefined });
            return { kind: 'table', data } as ToolResult;
          }}
        >
          <FileField fileRef={paymentFile} />
          <div className="space-y-1">
            <Label className="text-xs text-[#525252]">Sheet Name</Label>
            <Input value={paymentSheet} onChange={(e) => setPaymentSheet(e.target.value)} className="bg-white border-[#E5E5E5] text-sm" placeholder="Sheet1" />
          </div>
        </ToolCard>

        {/* Sum & Format */}
        <ToolCard
          title="Sum & Format"
          description="Sum a range of rows in a column and write the total to a target cell."
          onRun={async () => {
            const file = getFile(sumFile);
            const data = await api.sumAndFormat({
              file,
              sheet: sumSheet,
              startCell: sumStartCell,
              rowCount: parseInt(sumRowCount, 10),
              targetCol: sumTargetCol || undefined,
            });
            return { kind: 'sum', data } as ToolResult;
          }}
        >
          <FileField fileRef={sumFile} />
          <div className="grid grid-cols-2 gap-2">
            <div className="space-y-1">
              <Label className="text-xs text-[#525252]">Sheet</Label>
              <Input value={sumSheet} onChange={(e) => setSumSheet(e.target.value)} className="bg-white border-[#E5E5E5] text-sm" />
            </div>
            <div className="space-y-1">
              <Label className="text-xs text-[#525252]">Start Cell</Label>
              <Input value={sumStartCell} onChange={(e) => setSumStartCell(e.target.value)} className="bg-white border-[#E5E5E5] text-sm" placeholder="e.g. H10" />
            </div>
            <div className="space-y-1">
              <Label className="text-xs text-[#525252]">Row Count</Label>
              <Input type="number" value={sumRowCount} onChange={(e) => setSumRowCount(e.target.value)} className="bg-white border-[#E5E5E5] text-sm" />
            </div>
            <div className="space-y-1">
              <Label className="text-xs text-[#525252]">Target Col (default: H)</Label>
              <Input value={sumTargetCol} onChange={(e) => setSumTargetCol(e.target.value)} className="bg-white border-[#E5E5E5] text-sm" placeholder="H" />
            </div>
          </div>
        </ToolCard>

        {/* ACH Sum */}
        <ToolCard
          title="ACH Sum"
          description="Find last occurrence of search text and sum values from start row to that row."
          onRun={async () => {
            const file = getFile(achFile);
            const data = await api.achSum({
              file,
              sheet: achSheet,
              startCell: achStartCell,
              findText: achFindText || undefined,
            });
            return { kind: 'sum', data } as ToolResult;
          }}
        >
          <FileField fileRef={achFile} />
          <div className="grid grid-cols-2 gap-2">
            <div className="space-y-1">
              <Label className="text-xs text-[#525252]">Sheet</Label>
              <Input value={achSheet} onChange={(e) => setAchSheet(e.target.value)} className="bg-white border-[#E5E5E5] text-sm" />
            </div>
            <div className="space-y-1">
              <Label className="text-xs text-[#525252]">Start Cell</Label>
              <Input value={achStartCell} onChange={(e) => setAchStartCell(e.target.value)} className="bg-white border-[#E5E5E5] text-sm" placeholder="e.g. H5" />
            </div>
          </div>
          <div className="space-y-1">
            <Label className="text-xs text-[#525252]">Find Text (default: ACH Draft)</Label>
            <Input value={achFindText} onChange={(e) => setAchFindText(e.target.value)} className="bg-white border-[#E5E5E5] text-sm" placeholder="ACH Draft" />
          </div>
        </ToolCard>

        {/* One Cell */}
        <ToolCard
          title="One Cell"
          description="Copy a value from an offset cell to another offset cell relative to a start cell."
          onRun={async () => {
            const file = getFile(oneCellFile);
            const data = await api.oneCell({ file, sheet: oneCellSheet, startCell: oneCellStartCell });
            return { kind: 'simple', data } as ToolResult;
          }}
        >
          <FileField fileRef={oneCellFile} />
          <div className="grid grid-cols-2 gap-2">
            <div className="space-y-1">
              <Label className="text-xs text-[#525252]">Sheet</Label>
              <Input value={oneCellSheet} onChange={(e) => setOneCellSheet(e.target.value)} className="bg-white border-[#E5E5E5] text-sm" />
            </div>
            <div className="space-y-1">
              <Label className="text-xs text-[#525252]">Start Cell</Label>
              <Input value={oneCellStartCell} onChange={(e) => setOneCellStartCell(e.target.value)} className="bg-white border-[#E5E5E5] text-sm" placeholder="e.g. H10" />
            </div>
          </div>
        </ToolCard>

        {/* Find Duplicates */}
        <ToolCard
          title="Find Duplicates"
          description="Find duplicate values in a specified column."
          onRun={async () => {
            const file = getFile(dupsFile);
            const data = await api.findDups({ file, sheet: dupsSheet, lookupCol: dupsLookupCol || undefined });
            return { kind: 'dups', data } as ToolResult;
          }}
        >
          <FileField fileRef={dupsFile} />
          <div className="grid grid-cols-2 gap-2">
            <div className="space-y-1">
              <Label className="text-xs text-[#525252]">Sheet</Label>
              <Input value={dupsSheet} onChange={(e) => setDupsSheet(e.target.value)} className="bg-white border-[#E5E5E5] text-sm" />
            </div>
            <div className="space-y-1">
              <Label className="text-xs text-[#525252]">Lookup Column (default: F)</Label>
              <Input value={dupsLookupCol} onChange={(e) => setDupsLookupCol(e.target.value)} className="bg-white border-[#E5E5E5] text-sm" placeholder="F" />
            </div>
          </div>
        </ToolCard>

        {/* Cumulative Sum */}
        <ToolCard
          title="Cumulative Sum"
          description="Sum all values in a column from start row to end row (exclusive)."
          onRun={async () => {
            const file = getFile(cumsumFile);
            const data = await api.cumulativeSum({
              file,
              sheet: cumsumSheet,
              startRow: parseInt(cumsumStartRow, 10),
              startCol: parseInt(cumsumStartCol, 10),
              endRow: parseInt(cumsumEndRow, 10),
            });
            return { kind: 'cumsum', data } as ToolResult;
          }}
        >
          <FileField fileRef={cumsumFile} />
          <div className="grid grid-cols-2 gap-2">
            <div className="space-y-1">
              <Label className="text-xs text-[#525252]">Sheet</Label>
              <Input value={cumsumSheet} onChange={(e) => setCumsumSheet(e.target.value)} className="bg-white border-[#E5E5E5] text-sm" />
            </div>
            <div className="space-y-1">
              <Label className="text-xs text-[#525252]">Start Col (1-based number)</Label>
              <Input type="number" value={cumsumStartCol} onChange={(e) => setCumsumStartCol(e.target.value)} className="bg-white border-[#E5E5E5] text-sm" />
            </div>
            <div className="space-y-1">
              <Label className="text-xs text-[#525252]">Start Row</Label>
              <Input type="number" value={cumsumStartRow} onChange={(e) => setCumsumStartRow(e.target.value)} className="bg-white border-[#E5E5E5] text-sm" />
            </div>
            <div className="space-y-1">
              <Label className="text-xs text-[#525252]">End Row (exclusive)</Label>
              <Input type="number" value={cumsumEndRow} onChange={(e) => setCumsumEndRow(e.target.value)} className="bg-white border-[#E5E5E5] text-sm" />
            </div>
          </div>
        </ToolCard>
      </div>
    </div>
  );
}
