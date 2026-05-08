// Per-HOA static-appendix uploader (UI-SPEC follow-up).
//
// Lists currently-uploaded appendices, accepts new PDFs, and lets the
// operator delete any of them. The disclosure compiler appends every PDF
// in this list (sorted by filename) after the generated pages.

import { useCallback, useEffect, useRef, useState } from 'react';
import { Plus, Trash2, FileText, Loader2 } from 'lucide-react';

import {
  type DisclosureAppendixEntry,
  deleteDisclosureAppendix,
  listDisclosureAppendices,
  uploadDisclosureAppendix,
} from '../../api/disclosurePackage';
import { Button } from '../ui/button';

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
  const fileInputRef = useRef<HTMLInputElement | null>(null);

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
      if (fileInputRef.current) fileInputRef.current.value = '';
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
    <div className="space-y-3">
      <div className="flex items-center justify-between">
        <div>
          <h3 className="text-sm font-semibold text-[#111111]">Static appendices</h3>
          <p className="text-xs text-[#737373]">
            PDFs uploaded here are appended to every generated package, sorted by filename.
          </p>
        </div>
        <div>
          <input
            ref={fileInputRef}
            type="file"
            accept="application/pdf,.pdf"
            multiple
            className="hidden"
            onChange={(e) => void handleFiles(e.target.files)}
          />
          <Button
            variant="outline"
            disabled={disabled || uploading}
            onClick={() => fileInputRef.current?.click()}
            className="border-[#d4d4d4] text-[#111111] hover:border-[#a3a3a3] hover:bg-[#f5f5f5]"
          >
            {uploading ? (
              <Loader2 className="mr-2 h-4 w-4 animate-spin" />
            ) : (
              <Plus className="mr-2 h-4 w-4" />
            )}
            {uploading ? 'Uploading…' : 'Upload PDF'}
          </Button>
        </div>
      </div>

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
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
