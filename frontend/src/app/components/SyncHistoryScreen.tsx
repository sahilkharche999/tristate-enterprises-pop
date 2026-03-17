import { useState } from 'react';
import { useParams, Link } from 'react-router';
import { ArrowLeft, Download, Eye, RotateCcw, ChevronDown, ChevronRight, Settings } from 'lucide-react';
import { Button } from './ui/button';
import { hoaList } from '../data/mockData';
import { formatCurrency } from '../lib/budget';
import { toast } from 'sonner';
import { 
  AlertDialog,
  AlertDialogAction,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
} from './ui/alert-dialog';

interface SyncRecord {
  id: string;
  syncDate: string;
  fileName: string;
  uploadedBy: string;
  enrichmentStatus: string;
  version: string;
  canRestore: boolean;
}

interface EnrichmentLog {
  id: string;
  date: string;
  action: string;
  details?: {
    lineItems?: string[];
    confidence?: string;
  };
}

interface AnnotationRecord {
  id: string;
  date: string;
  noteType: string;
  lineItemName?: string;
  noteTitle: string;
  createdBy: string;
  noteBody: string;
}

interface BudgetSnapshot {
  id: string;
  version: string;
  generatedOn: string;
  totalIncome: number;
  totalExpense: number;
  netOperatingIncome: number;
  status: string;
}

const mockSyncRecords: SyncRecord[] = [
  {
    id: 'sync-1',
    syncDate: 'Jan 14, 2025',
    fileName: '401 - 2025 Income Statement FINAL.xlsx',
    uploadedBy: 'Bob Nattenberg',
    enrichmentStatus: 'Enriched Successfully',
    version: 'V1',
    canRestore: true,
  },
  {
    id: 'sync-2',
    syncDate: 'Dec 10, 2024',
    fileName: '401 - 2024 Income Statement.xlsx',
    uploadedBy: 'Admin',
    enrichmentStatus: 'Archived',
    version: 'V3',
    canRestore: false,
  },
  {
    id: 'sync-3',
    syncDate: 'Nov 22, 2024',
    fileName: '401 - 2024 Income Statement Draft.xlsx',
    uploadedBy: 'Bob Nattenberg',
    enrichmentStatus: 'Enriched Successfully',
    version: 'V2',
    canRestore: false,
  },
];

const mockEnrichmentLogs: EnrichmentLog[] = [
  {
    id: 'log-1',
    date: 'Jan 14, 2025',
    action: 'Macro v1.2 applied',
  },
  {
    id: 'log-2',
    date: 'Jan 14, 2025',
    action: 'AI Suggestions generated',
  },
  {
    id: 'log-3',
    date: 'Jan 14, 2025',
    action: '3 AI adjustments applied',
    details: {
      lineItems: ['Printing & Postage', 'Utilities – Electric', 'Insurance'],
      confidence: '76%–92%',
    },
  },
  {
    id: 'log-4',
    date: 'Jan 14, 2025',
    action: 'Draft Version 1 generated',
  },
];

const mockAnnotations: AnnotationRecord[] = [
  {
    id: 'note-1',
    date: 'Jan 14, 2025',
    noteType: 'Line Item',
    lineItemName: 'Insurance',
    noteTitle: 'Premium Increase Expected',
    createdBy: 'Bob Nattenberg',
    noteBody: 'Our insurance broker confirmed a 8-10% premium increase for the upcoming renewal period due to market conditions.',
  },
  {
    id: 'note-2',
    date: 'Jan 14, 2025',
    noteType: 'Global Context',
    noteTitle: '2025 Fiscal Strategy',
    createdBy: 'Bob Nattenberg',
    noteBody: 'Board approved conservative growth assumptions. Focus on maintaining adequate reserves while keeping assessment increases minimal.',
  },
  {
    id: 'note-3',
    date: 'Jan 13, 2025',
    noteType: 'Line Item',
    lineItemName: 'Repairs & Maintenance',
    noteTitle: 'Deferred Maintenance Catchup',
    createdBy: 'Admin',
    noteBody: 'Reserve study indicates need for increased maintenance funding to address deferred items from 2024.',
  },
];

const mockBudgetSnapshots: BudgetSnapshot[] = [
  {
    id: 'v1',
    version: 'V1',
    generatedOn: 'Jan 14, 2025',
    totalIncome: 1362988,
    totalExpense: 1136406,
    netOperatingIncome: 226582,
    status: 'Active',
  },
  {
    id: 'v2',
    version: 'V2',
    generatedOn: 'Jan 20, 2025',
    totalIncome: 1380120,
    totalExpense: 1150000,
    netOperatingIncome: 230120,
    status: 'Archived',
  },
];

export function SyncHistoryScreen() {
  const { id } = useParams<{ id: string }>();
  const hoa = hoaList.find((h) => h.id === id);
  
  const [expandedLogs, setExpandedLogs] = useState<string[]>([]);
  const [selectedNote, setSelectedNote] = useState<AnnotationRecord | null>(null);
  const [restoreDialogOpen, setRestoreDialogOpen] = useState(false);
  const [selectedRestore, setSelectedRestore] = useState<SyncRecord | null>(null);
  const [compareMode, setCompareMode] = useState(false);
  const [selectedSnapshots, setSelectedSnapshots] = useState<string[]>([]);

  if (!hoa) {
    return (
      <div className="min-h-screen bg-white flex items-center justify-center">
        <p className="text-[#666666]">HOA not found</p>
      </div>
    );
  }

  const toggleLogExpansion = (logId: string) => {
    setExpandedLogs((prev) =>
      prev.includes(logId) ? prev.filter((id) => id !== logId) : [...prev, logId]
    );
  };

  const handleView = (fileName: string) => {
    toast.success(`Viewing ${fileName}`);
  };

  const handleDownload = (fileName: string) => {
    toast.success('File downloaded successfully.');
  };

  const handleRestoreClick = (record: SyncRecord) => {
    setSelectedRestore(record);
    setRestoreDialogOpen(true);
  };

  const handleRestoreConfirm = () => {
    if (selectedRestore) {
      toast.success('Version restored successfully.');
      setRestoreDialogOpen(false);
      setSelectedRestore(null);
    }
  };

  const handleCompare = () => {
    if (selectedSnapshots.length === 2) {
      setCompareMode(true);
    } else {
      toast.error('Please select exactly 2 versions to compare');
    }
  };

  const toggleSnapshotSelection = (snapshotId: string) => {
    setSelectedSnapshots((prev) => {
      if (prev.includes(snapshotId)) {
        return prev.filter((id) => id !== snapshotId);
      }
      if (prev.length >= 2) {
        toast.error('You can only compare 2 versions at a time');
        return prev;
      }
      return [...prev, snapshotId];
    });
  };

  if (compareMode && selectedSnapshots.length === 2) {
    const snapshot1 = mockBudgetSnapshots.find((s) => s.id === selectedSnapshots[0])!;
    const snapshot2 = mockBudgetSnapshots.find((s) => s.id === selectedSnapshots[1])!;

    return (
      <div className="min-h-screen bg-white">
        {/* Header */}
        <header className="border-b border-[#e5e5e5] bg-white sticky top-0 z-10 shadow-sm">
          <div className="px-8 py-6">
            <div className="flex items-center justify-between">
              <div className="flex items-center gap-6">
                <button
                  onClick={() => {
                    setCompareMode(false);
                    setSelectedSnapshots([]);
                  }}
                  className="p-2 hover:bg-[#f5f5f5] rounded-lg transition-colors"
                >
                  <ArrowLeft className="w-5 h-5 text-[#525252]" />
                </button>
                <div>
                  <h1 className="text-xl font-semibold text-[#111111]">
                    Budget Version Comparison
                  </h1>
                  <p className="text-sm text-[#737373]">{hoa.name}</p>
                </div>
              </div>
            </div>
          </div>
        </header>

        {/* Comparison Content */}
        <main className="px-8 py-12 max-w-7xl mx-auto">
          <div className="bg-[#fafafa] border border-[#e5e5e5] rounded-lg overflow-hidden">
            <div className="px-8 py-6 bg-white border-b border-[#e5e5e5]">
              <h2 className="text-lg font-semibold text-[#111111]">Snapshot Comparison</h2>
              <p className="text-sm text-[#737373] mt-1">Compare key metrics between two budget versions</p>
            </div>
            <div className="p-8">
              <div className="grid grid-cols-2 gap-8">
                {/* Version 1 */}
                <div className="bg-white border border-[#e5e5e5] rounded-lg p-6">
                  <div className="pb-4 mb-6 border-b border-[#e5e5e5]">
                    <h3 className="text-base font-semibold text-[#111111] mb-1">
                      {snapshot1.version}
                    </h3>
                    <p className="text-sm text-[#737373]">Generated on {snapshot1.generatedOn}</p>
                  </div>
                  <div className="space-y-6">
                    <div className="pb-6 border-b border-[#e5e5e5]">
                      <p className="text-xs font-medium text-[#a3a3a3] uppercase tracking-wide mb-2">Total Income</p>
                      <p className="text-2xl font-semibold text-[#111111]">
                        {formatCurrency(snapshot1.totalIncome)}
                      </p>
                    </div>
                    <div className="pb-6 border-b border-[#e5e5e5]">
                      <p className="text-xs font-medium text-[#a3a3a3] uppercase tracking-wide mb-2">Total Expense</p>
                      <p className="text-2xl font-semibold text-[#111111]">
                        {formatCurrency(snapshot1.totalExpense)}
                      </p>
                    </div>
                    <div className="pb-6 border-b border-[#e5e5e5]">
                      <p className="text-xs font-medium text-[#a3a3a3] uppercase tracking-wide mb-2">Net Operating Income</p>
                      <p className="text-2xl font-semibold text-[#111111]">
                        {formatCurrency(snapshot1.netOperatingIncome)}
                      </p>
                    </div>
                  </div>
                </div>

                {/* Version 2 */}
                <div className="bg-white border border-[#e5e5e5] rounded-lg p-6">
                  <div className="pb-4 mb-6 border-b border-[#e5e5e5]">
                    <h3 className="text-base font-semibold text-[#111111] mb-1">
                      {snapshot2.version}
                    </h3>
                    <p className="text-sm text-[#737373]">Generated on {snapshot2.generatedOn}</p>
                  </div>
                  <div className="space-y-6">
                    <div className="pb-6 border-b border-[#e5e5e5]">
                      <p className="text-xs font-medium text-[#a3a3a3] uppercase tracking-wide mb-2">Total Income</p>
                      <p className="text-2xl font-semibold text-[#111111]">
                        {formatCurrency(snapshot2.totalIncome)}
                      </p>
                      {snapshot2.totalIncome !== snapshot1.totalIncome && (
                        <p className="text-sm text-[#525252] mt-2 font-medium">
                          Difference: {formatCurrency(snapshot2.totalIncome - snapshot1.totalIncome)}
                        </p>
                      )}
                    </div>
                    <div className="pb-6 border-b border-[#e5e5e5]">
                      <p className="text-xs font-medium text-[#a3a3a3] uppercase tracking-wide mb-2">Total Expense</p>
                      <p className="text-2xl font-semibold text-[#111111]">
                        {formatCurrency(snapshot2.totalExpense)}
                      </p>
                      {snapshot2.totalExpense !== snapshot1.totalExpense && (
                        <p className="text-sm text-[#525252] mt-2 font-medium">
                          Difference: {formatCurrency(snapshot2.totalExpense - snapshot1.totalExpense)}
                        </p>
                      )}
                    </div>
                    <div className="pb-6 border-b border-[#e5e5e5]">
                      <p className="text-xs font-medium text-[#a3a3a3] uppercase tracking-wide mb-2">Net Operating Income</p>
                      <p className="text-2xl font-semibold text-[#111111]">
                        {formatCurrency(snapshot2.netOperatingIncome)}
                      </p>
                      {snapshot2.netOperatingIncome !== snapshot1.netOperatingIncome && (
                        <p className="text-sm text-[#525252] mt-2 font-medium">
                          Difference:{' '}
                          {formatCurrency(snapshot2.netOperatingIncome - snapshot1.netOperatingIncome)}
                        </p>
                      )}
                    </div>
                  </div>
                </div>
              </div>
            </div>
          </div>
        </main>
      </div>
    );
  }

  return (
    <div className="min-h-screen bg-white">
      {/* Header */}
      <header className="border-b border-[#e5e5e5] bg-white sticky top-0 z-10 shadow-sm">
        <div className="px-8 py-6">
          <div className="flex items-center justify-between">
            <div className="flex items-center gap-6">
              <Link
                to={`/hoa/${id}`}
                className="p-2 hover:bg-[#f5f5f5] rounded-lg transition-colors"
              >
                <ArrowLeft className="w-5 h-5 text-[#525252]" />
              </Link>
              <div>
                <h1 className="text-xl font-semibold text-[#111111]">HOA Sync History</h1>
                <p className="text-sm text-[#737373]">
                  {hoa.name} • Fiscal Year: {hoa.fiscalYear}
                </p>
              </div>
            </div>
            <div className="flex items-center gap-4">
              <div className="text-right">
                <p className="text-xs text-[#a3a3a3]">Last Synced</p>
                <p className="text-sm text-[#525252] font-medium">Jan 14, 2025 at 2:34 PM</p>
              </div>
              <div className="h-8 w-px bg-[#e5e5e5]"></div>
              <Link to={`/hoa/${id}/settings`}>
                <Button variant="ghost" size="icon" className="hover:bg-[#f5f5f5]">
                  <Settings className="w-5 h-5 text-[#525252]" />
                </Button>
              </Link>
            </div>
          </div>
        </div>
      </header>

      {/* Main Content */}
      <main className="px-8 py-8 space-y-8">
        {/* Section 1: Uploaded File History */}
        <div className="bg-white border border-[#E5E5E5] rounded-lg overflow-hidden">
          <div className="p-6 border-b border-[#E5E5E5]">
            <h2 className="text-lg font-medium text-[#111111]">Income Statement Sync History</h2>
          </div>
          <div className="overflow-x-auto">
            <table className="w-full">
              <thead className="bg-[#F7F7F7] border-b border-[#E5E5E5]">
                <tr>
                  <th className="px-6 py-3 text-left text-xs font-medium text-[#666666]">
                    Sync Date
                  </th>
                  <th className="px-6 py-3 text-left text-xs font-medium text-[#666666]">
                    Uploaded File Name
                  </th>
                  <th className="px-6 py-3 text-left text-xs font-medium text-[#666666]">
                    Uploaded By
                  </th>
                  <th className="px-6 py-3 text-left text-xs font-medium text-[#666666]">
                    Enrichment Status
                  </th>
                  <th className="px-6 py-3 text-left text-xs font-medium text-[#666666]">
                    Version Generated
                  </th>
                  <th className="px-6 py-3 text-left text-xs font-medium text-[#666666]">
                    Actions
                  </th>
                </tr>
              </thead>
              <tbody className="divide-y divide-[#E5E5E5]">
                {mockSyncRecords.map((record) => (
                  <tr key={record.id} className="hover:bg-[#F7F7F7]">
                    <td className="px-6 py-4 text-sm text-[#111111]">{record.syncDate}</td>
                    <td className="px-6 py-4 text-sm text-[#111111]">{record.fileName}</td>
                    <td className="px-6 py-4 text-sm text-[#666666]">{record.uploadedBy}</td>
                    <td className="px-6 py-4">
                      <span className="inline-flex px-2 py-1 text-xs bg-[#F7F7F7] border border-[#E5E5E5] rounded">
                        {record.enrichmentStatus}
                      </span>
                    </td>
                    <td className="px-6 py-4 text-sm text-[#111111]">{record.version}</td>
                    <td className="px-6 py-4">
                      <div className="flex items-center gap-2">
                        <Button
                          variant="ghost"
                          size="sm"
                          onClick={() => handleView(record.fileName)}
                        >
                          <Eye className="w-4 h-4 mr-1" />
                          View
                        </Button>
                        <Button
                          variant="ghost"
                          size="sm"
                          onClick={() => handleDownload(record.fileName)}
                        >
                          <Download className="w-4 h-4 mr-1" />
                          Download
                        </Button>
                        {record.canRestore && (
                          <Button
                            variant="ghost"
                            size="sm"
                            onClick={() => handleRestoreClick(record)}
                          >
                            <RotateCcw className="w-4 h-4 mr-1" />
                            Restore
                          </Button>
                        )}
                      </div>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>

        {/* Section 2: Enrichment Log History */}
        <div className="bg-white border border-[#E5E5E5] rounded-lg overflow-hidden">
          <div className="p-6 border-b border-[#E5E5E5]">
            <h2 className="text-lg font-medium text-[#111111]">Enrichment Log</h2>
          </div>
          <div className="divide-y divide-[#E5E5E5]">
            {mockEnrichmentLogs.map((log) => (
              <div key={log.id}>
                <div
                  className="px-6 py-4 hover:bg-[#F7F7F7] cursor-pointer flex items-center justify-between"
                  onClick={() => log.details && toggleLogExpansion(log.id)}
                >
                  <div className="flex items-center gap-3">
                    {log.details && (
                      <>
                        {expandedLogs.includes(log.id) ? (
                          <ChevronDown className="w-4 h-4 text-[#666666]" />
                        ) : (
                          <ChevronRight className="w-4 h-4 text-[#666666]" />
                        )}
                      </>
                    )}
                    <span className="text-sm text-[#111111]">{log.action}</span>
                  </div>
                  <span className="text-xs text-[#666666]">{log.date}</span>
                </div>
                {log.details && expandedLogs.includes(log.id) && (
                  <div className="px-6 py-4 bg-[#F7F7F7] border-t border-[#E5E5E5]">
                    <p className="text-sm text-[#111111] mb-2">AI Suggestion Applied</p>
                    {log.details.lineItems && (
                      <div className="mb-2">
                        <p className="text-xs text-[#666666] mb-1">Line Items Affected:</p>
                        <ul className="list-disc list-inside text-sm text-[#111111]">
                          {log.details.lineItems.map((item, idx) => (
                            <li key={idx}>{item}</li>
                          ))}
                        </ul>
                      </div>
                    )}
                    {log.details.confidence && (
                      <p className="text-xs text-[#666666]">
                        Confidence Range: {log.details.confidence}
                      </p>
                    )}
                  </div>
                )}
              </div>
            ))}
          </div>
        </div>

        {/* Section 3: Annotation History */}
        <div className="bg-white border border-[#E5E5E5] rounded-lg overflow-hidden">
          <div className="p-6 border-b border-[#E5E5E5]">
            <h2 className="text-lg font-medium text-[#111111]">Line Item & Global Notes History</h2>
          </div>
          <div className="overflow-x-auto">
            <table className="w-full">
              <thead className="bg-[#F7F7F7] border-b border-[#E5E5E5]">
                <tr>
                  <th className="px-6 py-3 text-left text-xs font-medium text-[#666666]">Date</th>
                  <th className="px-6 py-3 text-left text-xs font-medium text-[#666666]">
                    Note Type
                  </th>
                  <th className="px-6 py-3 text-left text-xs font-medium text-[#666666]">
                    Line Item Name
                  </th>
                  <th className="px-6 py-3 text-left text-xs font-medium text-[#666666]">
                    Note Title
                  </th>
                  <th className="px-6 py-3 text-left text-xs font-medium text-[#666666]">
                    Created By
                  </th>
                  <th className="px-6 py-3 text-left text-xs font-medium text-[#666666]">View</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-[#E5E5E5]">
                {mockAnnotations.map((note) => (
                  <tr key={note.id} className="hover:bg-[#F7F7F7]">
                    <td className="px-6 py-4 text-sm text-[#111111]">{note.date}</td>
                    <td className="px-6 py-4">
                      <span className="inline-flex px-2 py-1 text-xs bg-[#F7F7F7] border border-[#E5E5E5] rounded">
                        {note.noteType}
                      </span>
                    </td>
                    <td className="px-6 py-4 text-sm text-[#666666]">
                      {note.lineItemName || '—'}
                    </td>
                    <td className="px-6 py-4 text-sm text-[#111111]">{note.noteTitle}</td>
                    <td className="px-6 py-4 text-sm text-[#666666]">{note.createdBy}</td>
                    <td className="px-6 py-4">
                      <Button variant="ghost" size="sm" onClick={() => setSelectedNote(note)}>
                        <Eye className="w-4 h-4 mr-1" />
                        View
                      </Button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>

        {/* Section 4: Generated Budget Snapshots */}
        <div className="bg-white border border-[#E5E5E5] rounded-lg overflow-hidden">
          <div className="p-6 border-b border-[#E5E5E5] flex items-center justify-between">
            <h2 className="text-lg font-medium text-[#111111]">Budget Versions</h2>
            {selectedSnapshots.length === 2 && (
              <Button
                onClick={handleCompare}
                className="bg-[#000000] text-white hover:bg-[#111111]"
              >
                Compare Selected
              </Button>
            )}
          </div>
          <div className="overflow-x-auto">
            <table className="w-full">
              <thead className="bg-[#F7F7F7] border-b border-[#E5E5E5]">
                <tr>
                  <th className="px-6 py-3 text-left text-xs font-medium text-[#666666]">
                    Select
                  </th>
                  <th className="px-6 py-3 text-left text-xs font-medium text-[#666666]">
                    Version
                  </th>
                  <th className="px-6 py-3 text-left text-xs font-medium text-[#666666]">
                    Generated On
                  </th>
                  <th className="px-6 py-3 text-left text-xs font-medium text-[#666666]">
                    Total Income
                  </th>
                  <th className="px-6 py-3 text-left text-xs font-medium text-[#666666]">
                    Total Expense
                  </th>
                  <th className="px-6 py-3 text-left text-xs font-medium text-[#666666]">
                    Net Operating Income
                  </th>
                  <th className="px-6 py-3 text-left text-xs font-medium text-[#666666]">
                    Status
                  </th>
                  <th className="px-6 py-3 text-left text-xs font-medium text-[#666666]">
                    Actions
                  </th>
                </tr>
              </thead>
              <tbody className="divide-y divide-[#E5E5E5]">
                {mockBudgetSnapshots.map((snapshot) => (
                  <tr key={snapshot.id} className="hover:bg-[#F7F7F7]">
                    <td className="px-6 py-4">
                      <input
                        type="checkbox"
                        checked={selectedSnapshots.includes(snapshot.id)}
                        onChange={() => toggleSnapshotSelection(snapshot.id)}
                        className="w-4 h-4 border-[#E5E5E5] rounded"
                      />
                    </td>
                    <td className="px-6 py-4 text-sm text-[#111111]">{snapshot.version}</td>
                    <td className="px-6 py-4 text-sm text-[#666666]">{snapshot.generatedOn}</td>
                    <td className="px-6 py-4 text-sm text-[#111111]">
                      {formatCurrency(snapshot.totalIncome)}
                    </td>
                    <td className="px-6 py-4 text-sm text-[#111111]">
                      {formatCurrency(snapshot.totalExpense)}
                    </td>
                    <td className="px-6 py-4 text-sm text-[#111111]">
                      {formatCurrency(snapshot.netOperatingIncome)}
                    </td>
                    <td className="px-6 py-4">
                      <span className="inline-flex px-2 py-1 text-xs bg-[#F7F7F7] border border-[#E5E5E5] rounded">
                        {snapshot.status}
                      </span>
                    </td>
                    <td className="px-6 py-4">
                      <div className="flex items-center gap-2">
                        <Button
                          variant="ghost"
                          size="sm"
                          onClick={() => toast.success('Viewing snapshot')}
                        >
                          View Snapshot
                        </Button>
                        <Button
                          variant="ghost"
                          size="sm"
                          onClick={() => toast.success('Restoring snapshot')}
                        >
                          <RotateCcw className="w-4 h-4 mr-1" />
                          Restore
                        </Button>
                      </div>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      </main>

      {/* Note View Side Panel */}
      {selectedNote && (
        <div className="fixed inset-0 bg-black bg-opacity-30 z-50 flex justify-end">
          <div className="w-full max-w-md bg-white h-full shadow-xl overflow-y-auto">
            <div className="p-6 border-b border-[#E5E5E5] flex items-center justify-between">
              <h3 className="text-lg font-medium text-[#111111]">Note Details</h3>
              <button
                onClick={() => setSelectedNote(null)}
                className="text-[#666666] hover:text-[#111111]"
              >
                ✕
              </button>
            </div>
            <div className="p-6 space-y-4">
              <div>
                <p className="text-xs text-[#666666] mb-1">Date</p>
                <p className="text-sm text-[#111111]">{selectedNote.date}</p>
              </div>
              <div>
                <p className="text-xs text-[#666666] mb-1">Note Type</p>
                <p className="text-sm text-[#111111]">{selectedNote.noteType}</p>
              </div>
              {selectedNote.lineItemName && (
                <div>
                  <p className="text-xs text-[#666666] mb-1">Line Item</p>
                  <p className="text-sm text-[#111111]">{selectedNote.lineItemName}</p>
                </div>
              )}
              <div>
                <p className="text-xs text-[#666666] mb-1">Note Title</p>
                <p className="text-sm text-[#111111]">{selectedNote.noteTitle}</p>
              </div>
              <div>
                <p className="text-xs text-[#666666] mb-1">Created By</p>
                <p className="text-sm text-[#111111]">{selectedNote.createdBy}</p>
              </div>
              <div>
                <p className="text-xs text-[#666666] mb-1">Note Content</p>
                <p className="text-sm text-[#111111]">{selectedNote.noteBody}</p>
              </div>
            </div>
          </div>
        </div>
      )}

      {/* Restore Confirmation Dialog */}
      <AlertDialog open={restoreDialogOpen} onOpenChange={setRestoreDialogOpen}>
        <AlertDialogContent className="bg-white">
          <AlertDialogHeader>
            <AlertDialogTitle className="text-[#111111]">Restore this synced version?</AlertDialogTitle>
            <AlertDialogDescription className="text-[#666666]">
              This will replace current enriched table values with data from{' '}
              {selectedRestore?.fileName}. Your projections will be recalculated automatically.
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel className="border-[#E5E5E5] text-[#111111] hover:bg-[#F7F7F7]">
              Cancel
            </AlertDialogCancel>
            <AlertDialogAction
              onClick={handleRestoreConfirm}
              className="bg-[#000000] text-white hover:bg-[#111111]"
            >
              Restore Version
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </div>
  );
}