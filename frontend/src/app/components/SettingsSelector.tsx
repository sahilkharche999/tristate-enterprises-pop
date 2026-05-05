import { useEffect, useState } from 'react';
import { Link } from 'react-router';
import { Search, Settings as SettingsIcon, ArrowLeft } from 'lucide-react';
import { Input } from './ui/input';
import { listHOAs } from '../api/hoa';
import { getErrorMessage } from '../lib/errors';
import { getStatusColor } from '../lib/statusColors';
import { toHOAViewModel, type HOAViewModel } from '../lib/hoa';

export function SettingsSelector() {
  const [searchQuery, setSearchQuery] = useState('');
  const [hoas, setHoas] = useState<HOAViewModel[]>([]);
  const [isLoading, setIsLoading] = useState(true);
  const [loadError, setLoadError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;

    async function loadPortfolio() {
      setIsLoading(true);
      setLoadError(null);
      try {
        const response = await listHOAs();
        if (!cancelled) {
          setHoas(response.map(toHOAViewModel));
        }
      } catch (error) {
        if (!cancelled) {
          setLoadError(getErrorMessage(error, 'Failed to load HOA settings records.'));
        }
      } finally {
        if (!cancelled) {
          setIsLoading(false);
        }
      }
    }

    loadPortfolio();
    return () => {
      cancelled = true;
    };
  }, []);

  const filteredHOAs = hoas.filter((hoa) =>
    hoa.name.toLowerCase().includes(searchQuery.toLowerCase())
  );

  return (
    <div className="min-h-screen bg-[#fafafa]">
      <header className="border-b border-[#e5e5e5] bg-white shadow-sm">
        <div className="px-8 py-6 flex items-center justify-between">
          <div className="flex items-center gap-6">
            <Link to="/workspace" className="p-2 hover:bg-[#f5f5f5] rounded-lg transition-colors">
              <ArrowLeft className="w-5 h-5 text-[#111111]" />
            </Link>
            <div>
              <h1 className="text-2xl font-semibold text-[#111111] tracking-tight">Settings</h1>
              <p className="text-sm text-[#737373] mt-0.5">Select an HOA to configure settings</p>
            </div>
          </div>
          <SettingsIcon className="w-6 h-6 text-[#525252]" />
        </div>
      </header>

      <main className="max-w-5xl mx-auto px-8 py-12">
        <div className="mb-10">
          <div className="relative">
            <Search className="absolute left-4 top-1/2 -translate-y-1/2 w-5 h-5 text-[#a3a3a3]" />
            <Input
              type="text"
              placeholder="Search HOA by name..."
              value={searchQuery}
              onChange={(e) => setSearchQuery(e.target.value)}
              className="pl-12 h-12 bg-white border-[#e5e5e5] text-[#111111] placeholder:text-[#a3a3a3] focus:border-[#737373] focus:ring-1 focus:ring-[#737373] shadow-sm"
            />
          </div>
        </div>

        {isLoading ? (
          <div className="bg-white border border-[#e5e5e5] rounded-lg p-8 text-sm text-[#737373] shadow-sm">
            Loading HOA settings...
          </div>
        ) : loadError ? (
          <div className="bg-white border border-[#e5e5e5] rounded-lg p-8 text-sm text-[#b91c1c] shadow-sm">
            {loadError}
          </div>
        ) : (
          <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-6">
            {filteredHOAs.map((hoa) => (
              <Link
                key={hoa.id}
                to={`/hoa/${hoa.id}/settings`}
                className="block bg-white border border-[#e5e5e5] rounded-lg p-6 hover:border-[#737373] hover:shadow-lg transition-all duration-200"
              >
                <div className="space-y-4">
                  <div>
                    <h3 className="text-lg font-semibold text-[#111111] mb-2 line-clamp-2">{hoa.name}</h3>
                    <div className={`inline-flex px-3 py-1.5 rounded-md text-xs font-medium border ${getStatusColor(hoa.status)}`}>
                      {hoa.status}
                    </div>
                  </div>
                  <div className="pt-4 border-t border-[#e5e5e5] space-y-2">
                    <div className="flex items-center justify-between text-sm">
                      <span className="text-[#737373]">Fiscal Year</span>
                      <span className="font-medium text-[#111111]">{hoa.fiscalYear}</span>
                    </div>
                    <div className="flex items-center justify-between text-sm">
                      <span className="text-[#737373]">Total Units</span>
                      <span className="font-medium text-[#111111]">{hoa.units}</span>
                    </div>
                  </div>
                </div>
              </Link>
            ))}
          </div>
        )}
      </main>
    </div>
  );
}
