import { useState } from 'react';
import { Link } from 'react-router';
import { Search, Settings, LayoutList, LayoutGrid, Filter, X } from 'lucide-react';
import { Input } from './ui/input';
import { Button } from './ui/button';
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from './ui/select';
import { hoaList } from '../data/mockData';

export function HOAWorkspace() {
  const [searchQuery, setSearchQuery] = useState('');
  const [viewMode, setViewMode] = useState<'list' | 'card'>('list');
  const [filterYear, setFilterYear] = useState<string>('all');
  const [filterStatus, setFilterStatus] = useState<string>('all');
  const [filterUnits, setFilterUnits] = useState<string>('all');
  const [filterCity, setFilterCity] = useState<string>('all');

  // Get unique years, unit ranges, and cities
  const uniqueYears = Array.from(new Set(hoaList.map((hoa) => hoa.year))).sort((a, b) => b - a);
  const uniqueCities = Array.from(new Set(hoaList.map((hoa) => hoa.city))).sort();

  const filteredHOAs = hoaList.filter((hoa) => {
    // Text search filter
    const matchesSearch = hoa.name.toLowerCase().includes(searchQuery.toLowerCase()) ||
                          hoa.fiscalYear.toLowerCase().includes(searchQuery.toLowerCase()) ||
                          hoa.units.toString().includes(searchQuery) ||
                          hoa.year.toString().includes(searchQuery) ||
                          hoa.city.toLowerCase().includes(searchQuery.toLowerCase());

    // Year filter
    const matchesYear = filterYear === 'all' || hoa.year.toString() === filterYear;

    // Status filter
    const matchesStatus = filterStatus === 'all' || hoa.status === filterStatus;

    // Units filter
    let matchesUnits = true;
    if (filterUnits === 'small') matchesUnits = hoa.units < 100;
    else if (filterUnits === 'medium') matchesUnits = hoa.units >= 100 && hoa.units < 150;
    else if (filterUnits === 'large') matchesUnits = hoa.units >= 150;

    // City filter
    const matchesCity = filterCity === 'all' || hoa.city === filterCity;

    return matchesSearch && matchesYear && matchesStatus && matchesUnits && matchesCity;
  });

  const activeFiltersCount = [filterYear !== 'all', filterStatus !== 'all', filterUnits !== 'all', filterCity !== 'all'].filter(Boolean).length;

  const clearAllFilters = () => {
    setFilterYear('all');
    setFilterStatus('all');
    setFilterUnits('all');
    setFilterCity('all');
  };

  const getStatusColor = (status: string) => {
    switch (status) {
      case 'Completed':
        return 'bg-[#d1fae5] text-[#065f46] border-[#a7f3d0]';
      case 'In Progress':
        return 'bg-[#dbeafe] text-[#1e40af] border-[#bfdbfe]';
      case 'Not Started':
        return 'bg-[#f5f5f5] text-[#525252] border-[#e5e5e5]';
      default:
        return 'bg-[#f5f5f5] text-[#525252] border-[#e5e5e5]';
    }
  };

  return (
    <div className="min-h-screen bg-[#fafafa]">
      {/* Header */}
      <header className="border-b border-[#e5e5e5] bg-white shadow-sm sticky top-0 z-10">
        <div className="px-8 py-6 flex items-center justify-between">
          <div>
            <h1 className="text-2xl font-semibold text-[#111111] tracking-tight">Tri-State Enterprises</h1>
            <p className="text-sm text-[#737373] mt-0.5">HOA Budget Management System</p>
          </div>
          <Link to="/settings">
            <Button variant="ghost" size="icon" className="hover:bg-[#f5f5f5]">
              <Settings className="w-5 h-5 text-[#525252]" />
            </Button>
          </Link>
        </div>
      </header>

      {/* Main Content */}
      <main className="max-w-7xl mx-auto px-8 py-12">
        {/* Search Bar & View Toggle */}
        <div className="mb-6 flex items-center gap-4">
          <div className="relative flex-1">
            <Search className="absolute left-4 top-1/2 -translate-y-1/2 w-5 h-5 text-[#a3a3a3]" />
            <Input
              type="text"
              placeholder="Search by name, city, year, units, or fiscal year..."
              value={searchQuery}
              onChange={(e) => setSearchQuery(e.target.value)}
              className="pl-12 h-12 bg-white border-[#e5e5e5] text-[#111111] placeholder:text-[#a3a3a3] focus:border-[#737373] focus:ring-1 focus:ring-[#737373] shadow-sm"
            />
          </div>
          <div className="flex items-center gap-1 bg-white border border-[#e5e5e5] rounded-lg p-1 shadow-sm">
            <Button
              variant={viewMode === 'list' ? 'default' : 'ghost'}
              size="sm"
              onClick={() => setViewMode('list')}
              className={
                viewMode === 'list'
                  ? 'bg-[#111111] text-white hover:bg-[#262626]'
                  : 'text-[#525252] hover:bg-[#f5f5f5] hover:text-[#111111]'
              }
            >
              <LayoutList className="w-4 h-4" />
            </Button>
            <Button
              variant={viewMode === 'card' ? 'default' : 'ghost'}
              size="sm"
              onClick={() => setViewMode('card')}
              className={
                viewMode === 'card'
                  ? 'bg-[#111111] text-white hover:bg-[#262626]'
                  : 'text-[#525252] hover:bg-[#f5f5f5] hover:text-[#111111]'
              }
            >
              <LayoutGrid className="w-4 h-4" />
            </Button>
          </div>
        </div>

        {/* Filters */}
        <div className="mb-6 flex items-center gap-4 flex-wrap">
          <div className="flex items-center gap-2 text-sm text-[#525252]">
            <Filter className="w-4 h-4" />
            <span className="font-medium">Filters:</span>
          </div>

          <Select value={filterYear} onValueChange={setFilterYear}>
            <SelectTrigger className="w-[140px] h-9 bg-white border-[#e5e5e5]">
              <SelectValue placeholder="Year" />
            </SelectTrigger>
            <SelectContent>
              <SelectItem value="all">All Years</SelectItem>
              {uniqueYears.map((year) => (
                <SelectItem key={year} value={year.toString()}>
                  {year}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>

          <Select value={filterStatus} onValueChange={setFilterStatus}>
            <SelectTrigger className="w-[160px] h-9 bg-white border-[#e5e5e5]">
              <SelectValue placeholder="Status" />
            </SelectTrigger>
            <SelectContent>
              <SelectItem value="all">All Status</SelectItem>
              <SelectItem value="Not Started">Not Started</SelectItem>
              <SelectItem value="In Progress">In Progress</SelectItem>
              <SelectItem value="Completed">Completed</SelectItem>
            </SelectContent>
          </Select>

          <Select value={filterUnits} onValueChange={setFilterUnits}>
            <SelectTrigger className="w-[160px] h-9 bg-white border-[#e5e5e5]">
              <SelectValue placeholder="Units" />
            </SelectTrigger>
            <SelectContent>
              <SelectItem value="all">All Units</SelectItem>
              <SelectItem value="small">Small (&lt; 100)</SelectItem>
              <SelectItem value="medium">Medium (100-149)</SelectItem>
              <SelectItem value="large">Large (≥ 150)</SelectItem>
            </SelectContent>
          </Select>

          <Select value={filterCity} onValueChange={setFilterCity}>
            <SelectTrigger className="w-[160px] h-9 bg-white border-[#e5e5e5]">
              <SelectValue placeholder="City" />
            </SelectTrigger>
            <SelectContent>
              <SelectItem value="all">All Cities</SelectItem>
              {uniqueCities.map((city) => (
                <SelectItem key={city} value={city}>
                  {city}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>

          {activeFiltersCount > 0 && (
            <Button
              variant="ghost"
              size="sm"
              onClick={clearAllFilters}
              className="h-9 text-[#525252] hover:text-[#111111] hover:bg-[#f5f5f5]"
            >
              <X className="w-4 h-4 mr-1" />
              Clear ({activeFiltersCount})
            </Button>
          )}

          <div className="ml-auto text-sm text-[#737373]">
            {filteredHOAs.length} {filteredHOAs.length === 1 ? 'HOA' : 'HOAs'}
          </div>
        </div>

        {/* List View */}
        {viewMode === 'list' && (
          <div className="space-y-3">
            {filteredHOAs.map((hoa) => (
              <Link
                key={hoa.id}
                to={`/hoa/${hoa.id}`}
                className="block bg-white border border-[#e5e5e5] rounded-lg p-6 hover:border-[#737373] hover:shadow-md transition-all duration-200"
              >
                <div className="flex items-center justify-between">
                  <div className="flex-1">
                    <h3 className="text-lg font-semibold text-[#111111] mb-1.5">{hoa.name}</h3>
                    <div className="flex items-center gap-4 text-sm text-[#737373]">
                      <span>{hoa.city}</span>
                      <span className="text-[#d4d4d4]">•</span>
                      <span>Year: {hoa.year}</span>
                      <span className="text-[#d4d4d4]">•</span>
                      <span>Fiscal: {hoa.fiscalYear}</span>
                      <span className="text-[#d4d4d4]">•</span>
                      <span>{hoa.units} Units</span>
                    </div>
                  </div>
                  <div className={`px-3 py-1.5 rounded-md text-xs font-medium border ${getStatusColor(hoa.status)}`}>
                    {hoa.status}
                  </div>
                </div>
              </Link>
            ))}
          </div>
        )}

        {/* Card View */}
        {viewMode === 'card' && (
          <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-6">
            {filteredHOAs.map((hoa) => (
              <Link
                key={hoa.id}
                to={`/hoa/${hoa.id}`}
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
                      <span className="text-[#737373]">City</span>
                      <span className="font-medium text-[#111111]">{hoa.city}</span>
                    </div>
                    <div className="flex items-center justify-between text-sm">
                      <span className="text-[#737373]">Year</span>
                      <span className="font-medium text-[#111111]">{hoa.year}</span>
                    </div>
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