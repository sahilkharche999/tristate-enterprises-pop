import { useEffect, useRef, useState } from 'react';
import { deleteHOALogo, hoaLogoUrl, uploadHOALogo } from '../api/hoaSettings';
import { authHeaders } from '../api/http';
import { Button } from './ui/button';

const ACCEPTED_TYPES = '.png,.jpg,.jpeg,.svg';

interface HOALogoUploadControlProps {
  hoaId: number;
  hasLogo: boolean;
  onChanged: (hasLogo: boolean) => void;
}

/** Per-HOA disclosure-package letterhead logo (task 2.4). The preview image
 * is fetched via an authenticated request (not a bare <img src>, since the
 * GET endpoint requires auth like every other HOA-scoped route in this app)
 * and rendered from a blob: object URL. */
export function HOALogoUploadControl({ hoaId, hasLogo, onChanged }: HOALogoUploadControlProps) {
  const [previewUrl, setPreviewUrl] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const fileInputRef = useRef<HTMLInputElement | null>(null);

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
      if (fileInputRef.current) fileInputRef.current.value = '';
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
      <div className="flex items-center gap-3">
        {previewUrl ? (
          <img
            src={previewUrl}
            alt="Configured HOA logo preview"
            className="h-12 w-12 object-contain border border-[#e5e5e5] rounded"
          />
        ) : (
          <div className="h-12 w-12 flex items-center justify-center border border-dashed border-[#d4d4d4] rounded text-[10px] text-[#a3a3a3]">
            Default
          </div>
        )}
        <input
          ref={fileInputRef}
          type="file"
          accept={ACCEPTED_TYPES}
          disabled={busy}
          onChange={(e) => {
            const file = e.target.files?.[0];
            if (file) void handleFile(file);
          }}
          className="text-xs"
        />
        {hasLogo ? (
          <Button variant="ghost" disabled={busy} onClick={() => void handleRemove()} className="text-xs text-[#b91c1c]">
            Remove
          </Button>
        ) : null}
      </div>
      {error ? <p className="text-xs text-[#b91c1c]">{error}</p> : null}
    </div>
  );
}
