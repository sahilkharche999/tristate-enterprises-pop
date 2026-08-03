// Per-HOA static-appendix uploader (UI-SPEC follow-up).
//
// Lists currently-uploaded appendices, accepts new PDFs, and lets the
// operator delete any of them. The disclosure compiler appends every PDF
// in this list (sorted by filename) after the generated pages.

import { useCallback, useEffect, useState } from 'react';
import { Download, Trash2, FileText } from 'lucide-react';

import {
  type DisclosureAppendixEntry,
  deleteDisclosureAppendix,
  downloadDisclosureAppendix,
  listDisclosureAppendices,
  uploadDisclosureAppendix,
} from '../../api/disclosurePackage';
import { Button } from '../ui/button';
import { FileDropzone } from '../fileDropzone';

export interface DisclosureAppendixManagerProps {
  hoaId: number;
  // Disable upload/delete while a generation job is in flight so the
  // appendix set doesn't shift mid-render.
  disabled?: boolean;
}

function formatBytes(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(0)} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}

export function DisclosureAppendixManager({
  hoaId,
  disabled = false,
}: DisclosureAppendixManagerProps) {
  const [items, setItems] = useState<DisclosureAppendixEntry[]>([]);
  const [loading, setLoading] = useState(true);
  const [uploading, setUploading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const refresh = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const list = await listDisclosureAppendices(hoaId);
      setItems(list);
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to load appendices');
    } finally {
      setLoading(false);
    }
  }, [hoaId]);

  useEffect(() => {
    void refresh();
  }, [refresh]);

  const handleFiles = async (files: FileList | null) => {
    if (!files || files.length === 0) return;
    setUploading(true);
    setError(null);
    try {
      for (const file of Array.from(files)) {
        await uploadDisclosureAppendix(hoaId, file);
      }
      await refresh();
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Upload failed');
    } finally {
      setUploading(false);
    }
  };

  const handleDelete = async (filename: string) => {
    setError(null);
    try {
      await deleteDisclosureAppendix(hoaId, filename);
      await refresh();
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Delete failed');
    }
  };

  return (
    <div className="space-y-3 rounded border border-[#fde68a] bg-[#fffbeb] p-3">
      <div className="flex items-center justify-between">
        <div>
          <h3 className="text-sm font-semibold text-[#111111]">
            Legacy static appendices (deprecated)
          </h3>
          <p className="text-xs text-[#737373]">
            Prefer <strong>Settings → Appendices</strong> with a display title — those names
            appear in the package table of contents. Files uploaded here use the filename only
            and are no longer merged when Settings appendices are in use.
          </p>
        </div>
      </div>

      <FileDropzone
        title="Upload static appendix PDFs"
        helper="These PDFs are appended to every generated package."
        accept="application/pdf,.pdf"
        multiple
        fileName={null}
        disabled={disabled || uploading}
        status={uploading ? 'attention' : 'idle'}
        statusMessage={uploading ? 'Uploading…' : undefined}
        actionLabel="Choose PDFs"
        onFilesSelected={(files) => void handleFiles(files)}
      />

      {error ? (
        <p className="rounded border border-[#fecaca] bg-[#fef2f2] px-3 py-2 text-xs text-[#b91c1c]">
          {error}
        </p>
      ) : null}

      {loading ? (
        <p className="text-xs text-[#737373]">Loading appendices…</p>
      ) : items.length === 0 ? (
        <p className="rounded border border-dashed border-[#d4d4d4] bg-[#fafafa] px-3 py-4 text-xs text-[#737373]">
          No appendices uploaded yet. The package will contain only the generated pages.
        </p>
      ) : (
        <ul className="divide-y divide-[#e5e5e5] rounded border border-[#e5e5e5] bg-white">
          {items.map((item) => (
            <li
              key={item.filename}
              className="flex items-center justify-between gap-3 px-3 py-2"
            >
              <div className="flex min-w-0 items-center gap-2">
                <FileText className="h-4 w-4 shrink-0 text-[#737373]" />
                <span className="truncate text-sm text-[#111111]">{item.filename}</span>
                <span className="shrink-0 text-xs text-[#737373]">
                  {formatBytes(item.size_bytes)}
                </span>
              </div>
              <div className="flex items-center gap-1 shrink-0">
                <Button
                  variant="ghost"
                  size="sm"
                  onClick={() =>
                    void downloadDisclosureAppendix(hoaId, item.filename).catch(() =>
                      alert('Download failed')
                    )
                  }
                  className="text-[#737373] hover:bg-[#f5f5f5]"
                  aria-label={`Download ${item.filename}`}
                >
                  <Download className="h-4 w-4" />
                </Button>
                <Button
                  variant="ghost"
                  size="sm"
                  disabled={disabled}
                  onClick={() => void handleDelete(item.filename)}
                  className="text-[#737373] hover:bg-[#fef2f2] hover:text-[#b91c1c]"
                  aria-label={`Remove ${item.filename}`}
                >
                  <Trash2 className="h-4 w-4" />
                </Button>
              </div>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
