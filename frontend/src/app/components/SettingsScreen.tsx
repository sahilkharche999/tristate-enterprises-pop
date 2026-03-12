import { useState } from 'react';
import { useParams, Link } from 'react-router';
import { ArrowLeft, Download, FolderOpen, Eye } from 'lucide-react';
import { Button } from './ui/button';
import { Input } from './ui/input';
import { Label } from './ui/label';
import { Tabs, TabsContent, TabsList, TabsTrigger } from './ui/tabs';
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from './ui/select';
import { hoaList, getKnowledgeBaseFolders } from '../data/mockData';
import { toast } from 'sonner';
import { MacroToolsPanel } from './MacroToolsPanel';

export function SettingsScreen() {
  const { id } = useParams<{ id: string }>();
  const hoa = hoaList.find((h) => h.id === id);

  const [hoaConfig, setHoaConfig] = useState({
    name: hoa?.name || '',
    hoaId: id || '',
    fiscalYearStart: hoa?.fiscalYearStart || 'January',
    fiscalYearEnd: hoa?.fiscalYearEnd || 'December',
    taxId: hoa?.taxId || '',
    units: hoa?.units || 0,
    allocationType: 'Flat',
    driveFolderPath: '/Tri-State/HOAs/401-HOA',
  });

  const [selectedFolder, setSelectedFolder] = useState<string | null>(null);

  // Get HOA-specific knowledge base folders
  const knowledgeBaseFolders = id ? getKnowledgeBaseFolders(id) : [];

  const handleSave = () => {
    toast.success('Settings saved successfully');
  };

  const macroVersions = [
    { version: 'v1.2', description: 'Inflation adjusted', date: 'Feb 15, 2025' },
    { version: 'v1.1', description: 'Historical average', date: 'Jan 10, 2025' },
    { version: 'v1.0', description: 'Base model', date: 'Dec 1, 2024' },
  ];

  if (!hoa) {
    return (
      <div className="min-h-screen bg-white flex items-center justify-center">
        <p className="text-[#666666]">HOA not found</p>
      </div>
    );
  }

  return (
    <div className="min-h-screen bg-white">
      {/* Header */}
      <header className="border-b border-[#e5e5e5] bg-white sticky top-0 z-10 shadow-sm">
        <div className="px-8 py-6 flex items-center justify-between">
          <div className="flex items-center gap-6">
            <Link to={`/hoa/${id}`} className="p-2 hover:bg-[#f5f5f5] rounded-lg transition-colors">
              <ArrowLeft className="w-5 h-5 text-[#525252]" />
            </Link>
            <div>
              <h1 className="text-xl font-semibold text-[#111111]">Settings</h1>
              <p className="text-sm text-[#737373]">{hoa.name}</p>
            </div>
          </div>
          <Button onClick={handleSave} className="bg-[#111111] text-white hover:bg-[#262626] shadow-sm">
            Save Changes
          </Button>
        </div>
      </header>

      {/* Main Content */}
      <main className="max-w-6xl mx-auto px-8 py-8">
        <Tabs defaultValue="database" className="space-y-8">
          <TabsList className="bg-[#F7F7F7] border border-[#E5E5E5]">
            <TabsTrigger value="database" className="data-[state=active]:bg-white">
              HOA Database Configuration
            </TabsTrigger>
            <TabsTrigger value="macro" className="data-[state=active]:bg-white">
              Macro & Logic Settings
            </TabsTrigger>
            <TabsTrigger value="knowledge" className="data-[state=active]:bg-white">
              Knowledge Base
            </TabsTrigger>
          </TabsList>

          {/* Tab 1: HOA Database Configuration */}
          <TabsContent value="database" className="space-y-6">
            <div className="bg-[#F7F7F7] border border-[#E5E5E5] rounded-lg p-8 space-y-6">
              <div className="grid grid-cols-2 gap-6">
                <div className="space-y-2">
                  <Label htmlFor="hoaName">HOA Name</Label>
                  <Input
                    id="hoaName"
                    value={hoaConfig.name}
                    onChange={(e) => setHoaConfig({ ...hoaConfig, name: e.target.value })}
                    className="bg-white border-[#E5E5E5]"
                  />
                </div>
                <div className="space-y-2">
                  <Label htmlFor="hoaId">HOA ID</Label>
                  <Input
                    id="hoaId"
                    value={hoaConfig.hoaId}
                    onChange={(e) => setHoaConfig({ ...hoaConfig, hoaId: e.target.value })}
                    className="bg-white border-[#E5E5E5]"
                  />
                </div>
              </div>

              <div className="grid grid-cols-2 gap-6">
                <div className="space-y-2">
                  <Label htmlFor="fiscalStart">Fiscal Year Start</Label>
                  <Select
                    value={hoaConfig.fiscalYearStart}
                    onValueChange={(value) => setHoaConfig({ ...hoaConfig, fiscalYearStart: value })}
                  >
                    <SelectTrigger id="fiscalStart" className="bg-white border-[#E5E5E5]">
                      <SelectValue />
                    </SelectTrigger>
                    <SelectContent>
                      {['January', 'February', 'March', 'April', 'May', 'June', 'July', 'August', 'September', 'October', 'November', 'December'].map(
                        (month) => (
                          <SelectItem key={month} value={month}>
                            {month}
                          </SelectItem>
                        )
                      )}
                    </SelectContent>
                  </Select>
                </div>
                <div className="space-y-2">
                  <Label htmlFor="fiscalEnd">Fiscal Year End</Label>
                  <Select
                    value={hoaConfig.fiscalYearEnd}
                    onValueChange={(value) => setHoaConfig({ ...hoaConfig, fiscalYearEnd: value })}
                  >
                    <SelectTrigger id="fiscalEnd" className="bg-white border-[#E5E5E5]">
                      <SelectValue />
                    </SelectTrigger>
                    <SelectContent>
                      {['January', 'February', 'March', 'April', 'May', 'June', 'July', 'August', 'September', 'October', 'November', 'December'].map(
                        (month) => (
                          <SelectItem key={month} value={month}>
                            {month}
                          </SelectItem>
                        )
                      )}
                    </SelectContent>
                  </Select>
                </div>
              </div>

              <div className="grid grid-cols-2 gap-6">
                <div className="space-y-2">
                  <Label htmlFor="taxId">Tax ID</Label>
                  <Input
                    id="taxId"
                    value={hoaConfig.taxId}
                    onChange={(e) => setHoaConfig({ ...hoaConfig, taxId: e.target.value })}
                    className="bg-white border-[#E5E5E5]"
                  />
                </div>
                <div className="space-y-2">
                  <Label htmlFor="units">Number of Units</Label>
                  <Input
                    id="units"
                    type="number"
                    value={hoaConfig.units}
                    onChange={(e) => setHoaConfig({ ...hoaConfig, units: parseInt(e.target.value) || 0 })}
                    className="bg-white border-[#E5E5E5]"
                  />
                </div>
              </div>

              <div className="space-y-2">
                <Label htmlFor="template">Budget Template Selector</Label>
                <Select defaultValue="standard">
                  <SelectTrigger id="template" className="bg-white border-[#E5E5E5]">
                    <SelectValue />
                  </SelectTrigger>
                  <SelectContent>
                    <SelectItem value="standard">Standard Budget Template</SelectItem>
                    <SelectItem value="detailed">Detailed Budget Template</SelectItem>
                    <SelectItem value="simplified">Simplified Budget Template</SelectItem>
                  </SelectContent>
                </Select>
              </div>

              <div className="space-y-2">
                <Label htmlFor="driveFolderPath">Google Drive Folder Path</Label>
                <Input
                  id="driveFolderPath"
                  value={hoaConfig.driveFolderPath}
                  onChange={(e) => setHoaConfig({ ...hoaConfig, driveFolderPath: e.target.value })}
                  className="bg-white border-[#E5E5E5]"
                />
                <p className="text-xs text-[#666666]">Path to your HOA's Google Drive folder for document storage</p>
              </div>
            </div>
          </TabsContent>

          {/* Tab 2: Macro & Logic Settings */}
          <TabsContent value="macro" className="space-y-6">
            <div className="bg-[#F7F7F7] border border-[#E5E5E5] rounded-lg p-8 space-y-8">
              {/* Current Macro */}
              <div className="space-y-4">
                <h3 className="text-lg font-medium text-[#111111]">Current Macro Version</h3>
                <div className="bg-white border border-[#E5E5E5] rounded-lg p-6">
                  <div className="flex items-center justify-between">
                    <div>
                      <div className="text-sm font-medium text-[#111111]">Deterministic Macro v1.2</div>
                      <div className="text-sm text-[#666666] mt-1">Last Updated: Feb 15, 2025</div>
                    </div>
                    <Button variant="outline" className="border-[#E5E5E5]">
                      <Download className="w-4 h-4 mr-2" />
                      Download
                    </Button>
                  </div>
                </div>
              </div>

              {/* Macro Tools Panel */}
              <MacroToolsPanel />

              {/* Logic Formulas */}
              <div className="space-y-4">
                <h3 className="text-lg font-medium text-[#111111]">Growth Factor Logic Formula</h3>
                <div className="bg-white border border-[#E5E5E5] rounded-lg p-6">
                  <code className="text-sm text-[#111111] font-mono">
                    Projection = (YTD Actual / Months Elapsed) × 12
                    <br />
                    Proposed Change = Projection × (1 + % Change / 100)
                    <br />
                    Monthly Allocation = Proposed Change / 12
                  </code>
                </div>
              </div>

              {/* Reserve Logic */}
              <div className="space-y-4">
                <h3 className="text-lg font-medium text-[#111111]">Reserve Allocation Logic</h3>
                <div className="bg-white border border-[#E5E5E5] rounded-lg p-6">
                  <code className="text-sm text-[#111111] font-mono">
                    Total Reserve = Sum(All Reserve Line Items)
                    <br />
                    Reserve % of Budget = (Total Reserve / Total Annual Budget) × 100
                    <br />
                    Minimum Recommended Reserve = 10% of Total Budget
                  </code>
                </div>
              </div>

              {/* Version History */}
              <div className="space-y-4">
                <h3 className="text-lg font-medium text-[#111111]">Version History</h3>
                <div className="bg-white border border-[#E5E5E5] rounded-lg overflow-hidden">
                  <table className="w-full">
                    <thead className="bg-[#F7F7F7] border-b border-[#E5E5E5]">
                      <tr>
                        <th className="text-left px-6 py-3 text-sm font-medium text-[#111111]">Version</th>
                        <th className="text-left px-6 py-3 text-sm font-medium text-[#111111]">Description</th>
                        <th className="text-left px-6 py-3 text-sm font-medium text-[#111111]">Date</th>
                        <th className="text-right px-6 py-3 text-sm font-medium text-[#111111]">Action</th>
                      </tr>
                    </thead>
                    <tbody>
                      {macroVersions.map((version) => (
                        <tr key={version.version} className="border-b border-[#E5E5E5]">
                          <td className="px-6 py-4 text-sm text-[#111111]">{version.version}</td>
                          <td className="px-6 py-4 text-sm text-[#666666]">{version.description}</td>
                          <td className="px-6 py-4 text-sm text-[#666666]">{version.date}</td>
                          <td className="px-6 py-4 text-right">
                            <div className="flex items-center justify-end gap-1">
                              <Button variant="ghost" size="sm" className="text-[#111111]">
                                <Eye className="w-4 h-4" />
                              </Button>
                              <Button variant="ghost" size="sm" className="text-[#111111]">
                                <Download className="w-4 h-4" />
                              </Button>
                            </div>
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              </div>
            </div>
          </TabsContent>

          {/* Tab 3: Knowledge Base */}
          <TabsContent value="knowledge" className="space-y-6">
            <div className="grid grid-cols-4 gap-6">
              {/* Folder List */}
              <div className="col-span-1 bg-[#F7F7F7] border border-[#E5E5E5] rounded-lg p-4">
                <h3 className="text-sm font-medium text-[#111111] mb-4">Folders</h3>
                <div className="space-y-2">
                  {knowledgeBaseFolders.map((folder) => (
                    <button
                      key={folder.id}
                      onClick={() => setSelectedFolder(folder.id)}
                      className={`w-full text-left px-3 py-2 rounded-lg text-sm transition-colors ${
                        selectedFolder === folder.id
                          ? 'bg-[#000000] text-white'
                          : 'text-[#111111] hover:bg-white'
                      }`}
                    >
                      <div className="flex items-center gap-2">
                        <FolderOpen className="w-4 h-4" />
                        <span>{folder.name}</span>
                      </div>
                    </button>
                  ))}
                </div>
              </div>

              {/* File List */}
              <div className="col-span-3">
                {selectedFolder ? (
                  <div className="bg-[#F7F7F7] border border-[#E5E5E5] rounded-lg overflow-hidden">
                    <div className="px-6 py-4 border-b border-[#E5E5E5] bg-white">
                      <h3 className="text-lg font-medium text-[#111111]">
                        {knowledgeBaseFolders.find((f) => f.id === selectedFolder)?.name}
                      </h3>
                    </div>
                    <table className="w-full">
                      <thead className="bg-[#F7F7F7] border-b border-[#E5E5E5]">
                        <tr>
                          <th className="text-left px-6 py-3 text-sm font-medium text-[#111111]">File Name</th>
                          <th className="text-left px-6 py-3 text-sm font-medium text-[#111111]">Year</th>
                          <th className="text-left px-6 py-3 text-sm font-medium text-[#111111]">Status</th>
                          <th className="text-right px-6 py-3 text-sm font-medium text-[#111111]">Action</th>
                        </tr>
                      </thead>
                      <tbody className="bg-white">
                        {knowledgeBaseFolders
                          .find((f) => f.id === selectedFolder)
                          ?.files.map((file) => (
                            <tr key={file.id} className="border-b border-[#E5E5E5] hover:bg-[#FAFAFA]">
                              <td className="px-6 py-4 text-sm text-[#111111]">{file.name}</td>
                              <td className="px-6 py-4 text-sm text-[#666666]">{file.year}</td>
                              <td className="px-6 py-4 text-sm text-[#666666]">{file.status}</td>
                              <td className="px-6 py-4 text-right">
                                <div className="flex items-center justify-end gap-1">
                                  <Button variant="ghost" size="sm" className="text-[#111111] hover:bg-[#F7F7F7]">
                                    <Eye className="w-4 h-4" />
                                  </Button>
                                  <Button variant="ghost" size="sm" className="text-[#111111] hover:bg-[#F7F7F7]">
                                    <Download className="w-4 h-4" />
                                  </Button>
                                </div>
                              </td>
                            </tr>
                          ))}
                      </tbody>
                    </table>
                  </div>
                ) : (
                  <div className="bg-[#F7F7F7] border border-[#E5E5E5] rounded-lg h-96 flex items-center justify-center">
                    <p className="text-[#666666]">Select a folder to view files</p>
                  </div>
                )}
              </div>
            </div>
          </TabsContent>
        </Tabs>
      </main>
    </div>
  );
}