import { useEffect, useState } from 'react';
import { ChevronDown, ChevronUp } from 'lucide-react';
import {
  deleteFirmSignature,
  firmSignatureUrl,
  getAppSettings,
  type SectionCatalogItem,
  updateAppSettings,
  uploadFirmSignature,
} from '../api/appSettings';
import { authHeaders } from '../api/http';
import { getErrorMessage } from '../lib/errors';
import { FileDropzone } from './fileDropzone';
import { Button } from './ui/button';
import { toast } from 'sonner';

export function CompanyPackageSettingsCard() {
  const [rows, setRows] = useState<SectionCatalogItem[]>([]);
  const [hasSignature, setHasSignature] = useState(false);
  const [previewUrl, setPreviewUrl] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [dragIndex, setDragIndex] = useState<number | null>(null);

  const load = async () => {
    const settings = await getAppSettings();
    setRows(settings.section_catalog ?? []);
    setHasSignature(Boolean(settings.has_firm_signature));
  };

  useEffect(() => {
    void load().catch((error) => {
      toast.error(getErrorMessage(error, 'Failed to load company package settings.'));
    });
  }, []);

  useEffect(() => {
    let objectUrl: string | null = null;
    let cancelled = false;
    if (!hasSignature) {
      setPreviewUrl(null);
      return;
    }
    void fetch(firmSignatureUrl(), { headers: authHeaders() })
      .then((r) => (r.ok ? r.blob() : Promise.reject(new Error('Failed to load signature'))))
      .then((blob) => {
        if (cancelled) return;
        objectUrl = URL.createObjectURL(blob);
        setPreviewUrl(objectUrl);
      })
      .catch(() => setPreviewUrl(null));
    return () => {
      cancelled = true;
      if (objectUrl) URL.revokeObjectURL(objectUrl);
    };
  }, [hasSignature]);

  const persistRows = async (next: SectionCatalogItem[]) => {
    setBusy(true);
    try {
      const updated = await updateAppSettings({
        global_reserve_inflation_rate: (await getAppSettings()).global_reserve_inflation_rate ?? 0,
        disclosure_section_order: next.map((row) => row.template),
        disclosure_hidden_sections: next.filter((row) => row.hidden).map((row) => row.template),
      });
      setRows(updated.section_catalog ?? next);
      toast.success('Package order saved.');
    } catch (error) {
      toast.error(getErrorMessage(error, 'Failed to save package order.'));
    } finally {
      setBusy(false);
    }
  };

  const move = (index: number, delta: number) => {
    const next = [...rows];
    const target = index + delta;
    if (target < 0 || target >= next.length) return;
    const [row] = next.splice(index, 1);
    next.splice(target, 0, row);
    void persistRows(next);
  };

  const toggleHidden = (index: number) => {
    const row = rows[index];
    if (!row || row.required) return;
    const next = rows.map((item, i) =>
      i === index ? { ...item, hidden: !item.hidden } : item,
    );
    void persistRows(next);
  };

  const resetDefault = async () => {
    setBusy(true);
    try {
      const current = await getAppSettings();
      const updated = await updateAppSettings({
        global_reserve_inflation_rate: current.global_reserve_inflation_rate ?? 0,
        disclosure_section_order: [],
        disclosure_hidden_sections: [],
      });
      setRows(updated.section_catalog ?? []);
      toast.success('Reset to the Tri-State default order.');
    } catch (error) {
      toast.error(getErrorMessage(error, 'Failed to reset package order.'));
    } finally {
      setBusy(false);
    }
  };

  const handleDrop = (from: number, to: number) => {
    if (from === to) return;
    const next = [...rows];
    const [row] = next.splice(from, 1);
    next.splice(to, 0, row);
    void persistRows(next);
  };

  return (
    <div className="space-y-4 rounded-lg border border-[#e5e5e5] bg-white p-4">
      <div className="flex items-start justify-between gap-3">
        <div>
          <h3 className="text-sm font-semibold text-[#111111]">Company package order</h3>
          <p className="mt-1 text-xs text-[#737373]">
            Every HOA uses this sequence. Required pages stay visible. Hide optional
            sections you do not want in the packet.
          </p>
        </div>
        <Button type="button" variant="outline" size="sm" disabled={busy} onClick={() => void resetDefault()}>
          Reset default
        </Button>
      </div>
      <ol className="space-y-1">
        {rows.map((row, index) => (
          <li
            key={row.template}
            draggable
            onDragStart={() => setDragIndex(index)}
            onDragOver={(e) => e.preventDefault()}
            onDrop={() => {
              if (dragIndex !== null) handleDrop(dragIndex, index);
              setDragIndex(null);
            }}
            className={`flex items-center gap-2 rounded border px-2 py-1.5 text-sm ${
              row.hidden ? 'border-dashed border-[#d4d4d4] bg-[#fafafa] text-[#a3a3a3]' : 'border-[#e5e5e5] bg-white'
            }`}
          >
            <span className="min-w-0 flex-1 truncate">{row.label}</span>
            {row.required ? (
              <span className="text-[10px] uppercase tracking-wide text-[#737373]">Required</span>
            ) : (
              <label className="flex items-center gap-1 text-xs text-[#525252]">
                <input
                  type="checkbox"
                  checked={!row.hidden}
                  disabled={busy}
                  onChange={() => toggleHidden(index)}
                />
                Include
              </label>
            )}
            <button
              type="button"
              className="rounded p-1 text-[#525252] hover:bg-[#f5f5f5]"
              disabled={busy || index === 0}
              onClick={() => move(index, -1)}
              aria-label={`Move ${row.label} up`}
            >
              <ChevronUp className="h-4 w-4" />
            </button>
            <button
              type="button"
              className="rounded p-1 text-[#525252] hover:bg-[#f5f5f5]"
              disabled={busy || index === rows.length - 1}
              onClick={() => move(index, 1)}
              aria-label={`Move ${row.label} down`}
            >
              <ChevronDown className="h-4 w-4" />
            </button>
          </li>
        ))}
      </ol>
      <div className="space-y-2 border-t border-[#e5e5e5] pt-3">
        <h4 className="text-sm font-semibold text-[#111111]">Firm signature image</h4>
        <p className="text-xs text-[#737373]">
          Used on the cover letter closer for every HOA unless that HOA uploads its own.
          PNG or JPEG only.
        </p>
        <div className="flex items-start gap-3">
          {previewUrl ? (
            <img
              src={previewUrl}
              alt="Firm signature preview"
              className="h-12 max-w-[8rem] object-contain border border-[#e5e5e5] rounded bg-white"
            />
          ) : (
            <div className="h-12 w-20 shrink-0 flex items-center justify-center border border-dashed border-[#d4d4d4] rounded text-[10px] text-[#a3a3a3]">
              None
            </div>
          )}
          <div className="min-w-0 flex-1 space-y-2">
            <FileDropzone
              title="Signature scan"
              helper="PNG or JPEG."
              accept=".png,.jpg,.jpeg"
              fileName={hasSignature ? 'Current signature' : null}
              disabled={busy}
              status={hasSignature ? 'selected' : 'idle'}
              actionLabel="Choose signature"
              onFilesSelected={(files) => {
                const file = files?.[0];
                if (!file) return;
                setBusy(true);
                void uploadFirmSignature(file)
                  .then((updated) => {
                    setHasSignature(Boolean(updated.has_firm_signature));
                    toast.success('Firm signature saved.');
                  })
                  .catch((error) => {
                    toast.error(getErrorMessage(error, 'Failed to upload signature.'));
                  })
                  .finally(() => setBusy(false));
              }}
            />
            {hasSignature ? (
              <Button
                type="button"
                variant="ghost"
                size="sm"
                disabled={busy}
                className="h-8 px-2 text-xs text-[#b91c1c] hover:bg-[#fef2f2]"
                onClick={() => {
                  setBusy(true);
                  void deleteFirmSignature()
                    .then((updated) => {
                      setHasSignature(Boolean(updated.has_firm_signature));
                      toast.success('Firm signature removed.');
                    })
                    .catch((error) => {
                      toast.error(getErrorMessage(error, 'Failed to remove signature.'));
                    })
                    .finally(() => setBusy(false));
                }}
              >
                Remove signature
              </Button>
            ) : null}
          </div>
        </div>
      </div>
    </div>
  );
}
