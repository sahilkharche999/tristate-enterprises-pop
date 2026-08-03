import { useEffect, useState } from 'react';
import { deleteHOALogo, hoaLogoUrl, uploadHOALogo } from '../api/hoaSettings';
import { authHeaders } from '../api/http';
import { Button } from './ui/button';
import { FileDropzone } from './fileDropzone';

const ACCEPTED_TYPES = '.png,.jpg,.jpeg,.svg';

type LogoMode = 'logo_and_text' | 'logo_only';

interface HOALogoUploadControlProps {
  hoaId: number;
  hasLogo: boolean;
  logoMode?: LogoMode;
  onChanged: (hasLogo: boolean) => void;
  onLogoModeChanged?: (mode: LogoMode) => void;
}

/** Per-HOA disclosure-package letterhead logo (task 2.4). The preview image
 * is fetched via an authenticated request (not a bare <img src>, since the
 * GET endpoint requires auth like every other HOA-scoped route in this app)
 * and rendered from a blob: object URL. */
export function HOALogoUploadControl({
  hoaId,
  hasLogo,
  logoMode = 'logo_and_text',
  onChanged,
  onLogoModeChanged,
}: HOALogoUploadControlProps) {
  const [previewUrl, setPreviewUrl] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let objectUrl: string | null = null;
    let cancelled = false;
    if (hasLogo) {
      void fetch(hoaLogoUrl(hoaId), { headers: authHeaders() })
        .then((r) => (r.ok ? r.blob() : Promise.reject(new Error('Failed to load logo'))))
        .then((blob) => {
          if (cancelled) return;
          objectUrl = URL.createObjectURL(blob);
          setPreviewUrl(objectUrl);
        })
        .catch(() => setPreviewUrl(null));
    } else {
      setPreviewUrl(null);
    }
    return () => {
      cancelled = true;
      if (objectUrl) URL.revokeObjectURL(objectUrl);
    };
  }, [hoaId, hasLogo]);

  const handleFile = async (file: File) => {
    setBusy(true);
    setError(null);
    try {
      const updated = await uploadHOALogo(hoaId, file);
      onChanged(updated.has_logo);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  };

  const handleRemove = async () => {
    setBusy(true);
    setError(null);
    try {
      const updated = await deleteHOALogo(hoaId);
      onChanged(updated.has_logo);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="space-y-2 border border-[#e5e5e5] rounded p-3">
      <div>
        <h4 className="text-sm font-semibold text-[#111]">Disclosure package logo</h4>
        <p className="text-xs text-[#737373]">
          Replaces the default TriState mark in the letterhead on every page. Leave unset to use
          the default.
        </p>
      </div>
      <div className="space-y-1 rounded border border-[#e5e5e5] bg-[#fafafa] px-3 py-2">
        <p className="text-xs font-medium text-[#404040]">Letterhead layout</p>
        <p className="text-[11px] text-[#737373]">
          Use <strong>Logo only</strong> when the image already includes the company name (full
          brand lockup). Use <strong>Logo + text</strong> for a small mark next to company text.
        </p>
        <div className="flex flex-wrap gap-3 text-xs text-[#262626]">
          <label className="inline-flex items-center gap-1.5">
            <input
              type="radio"
              name="letterhead-logo-mode"
              checked={logoMode === 'logo_and_text'}
              disabled={busy}
              onChange={() => onLogoModeChanged?.('logo_and_text')}
            />
            Logo + company text
          </label>
          <label className="inline-flex items-center gap-1.5">
            <input
              type="radio"
              name="letterhead-logo-mode"
              checked={logoMode === 'logo_only'}
              disabled={busy}
              onChange={() => onLogoModeChanged?.('logo_only')}
            />
            Logo only (full brand image)
          </label>
        </div>
      </div>
      <div className="flex items-start gap-3">
        {previewUrl ? (
          <img
            src={previewUrl}
            alt="Configured HOA logo preview"
            className="h-12 w-12 shrink-0 object-contain border border-[#e5e5e5] rounded"
          />
        ) : (
          <div className="h-12 w-12 shrink-0 flex items-center justify-center border border-dashed border-[#d4d4d4] rounded text-[10px] text-[#a3a3a3]">
            Default
          </div>
        )}
        <div className="min-w-0 flex-1 space-y-2">
          <FileDropzone
            title="Logo image"
            helper="PNG, JPG, or SVG."
            accept={ACCEPTED_TYPES}
            fileName={hasLogo ? 'Current logo' : null}
            disabled={busy}
            status={error ? 'error' : hasLogo ? 'selected' : 'idle'}
            statusMessage={busy ? 'Uploading…' : error ?? undefined}
            actionLabel="Choose logo"
            onFilesSelected={(files) => {
              const file = files?.[0];
              if (file) void handleFile(file);
            }}
          />
          {hasLogo ? (
            <Button
              variant="ghost"
              size="sm"
              disabled={busy}
              onClick={() => void handleRemove()}
              className="h-8 px-2 text-xs text-[#b91c1c] hover:bg-[#fef2f2]"
            >
              Remove logo
            </Button>
          ) : null}
        </div>
      </div>
    </div>
  );
}
