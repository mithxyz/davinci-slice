local fu=fusion or fu or bmd.scriptapp('Fusion')
local r=bmd.scriptapp('Resolve')
local p=r:GetProjectManager():GetCurrentProject()
local t=p:GetCurrentTimeline()
assert(t:GetName()=='Map Remapper — Validation','Validation timeline only')
local c=fu:GetCurrentComp()
local source=assert(c:FindTool('MediaIn1'))
local output=assert(c:FindTool('MediaOut1'))
local base="/Users/mattosborne/M-projects/Claude/Davinci Slice/validation/remapper"
local cases={{name="native_forward",width=3840,height=2160},{name="native_reverse",width=3840,height=2160},{name="subset",width=3840,height=2160},{name="native_empty",width=3840,height=2160},{name="ultra_center_forward",width=3840,height=2160},{name="ultra_center_reverse",width=3840,height=2160},{name="ultra_sr_forward",width=3840,height=2160},{name="ultra_sr_reverse",width=3840,height=2160},{name="small_forward",width=800,height=400},{name="small_reverse",width=640,height=360}}
local f=assert(io.open(base..'/native_run.tsv','w'))
local function clear()
 output:FindMainInput(1):ConnectTo(source:FindMainOutput(1))
 for _,name in ipairs({'MapRemapper','RemapperAffineProbe'}) do
  local tool=c:FindTool(name);if tool then tool:Delete() end
 end
end
local function load(name)
 clear()
 assert(c:Paste(assert(bmd.readfile(base..'/cases/'..name..'.setting'))))
 local mapper=assert(c:FindTool('MapRemapper'))
 assert(mapper:FindMainInput(1):ConnectTo(source:FindMainOutput(1)))
 assert(output:FindMainInput(1):ConnectTo(mapper:FindMainOutput(1)))
 local metadata=assert(io.open(base..'/cases/'..name..'.json','rb'))
 local manifest=metadata:read('*a');metadata:close()
 mapper:SetData('DavinciRemap.Manifest',manifest)
 mapper:SetData('DavinciRemap.SourceHash',manifest:match('"source_sha256":%s*"([^"]+)"'))
 return mapper
end
local function render(name,w,h)
 t:SetSetting('useCustomSettings','1')
 assert(t:SetSetting('timelineResolutionWidth',tostring(w)))
 assert(t:SetSetting('timelineResolutionHeight',tostring(h)))
 assert(p:SetCurrentRenderMode(1))
 assert(p:SetCurrentRenderFormatAndCodec('png','RGB8'))
 assert(p:SetRenderSettings({TargetDir=base,CustomName=name,SelectAllFrames=false,MarkIn=t:GetStartFrame(),MarkOut=t:GetStartFrame(),ExportVideo=true,ExportAudio=false,ExportAlpha=true,FormatWidth=w,FormatHeight=h}))
 local id=assert(p:AddRenderJob())
 assert(p:StartRendering({id}))
 while p:IsRenderingInProgress() do bmd.wait(.1) end
 local status=p:GetRenderJobStatus(id)
 f:write(name..'\t'..tostring(status.JobStatus)..'\n');f:flush()
 assert(status.JobStatus=='Complete',name..' render failed')
 p:DeleteRenderJob(id)
 print('[Map Remapper validation] '..name..' complete')
end
local ok,err=pcall(function()
 clear();render('baseline',3840,2160)
 for _,case in ipairs(cases) do load(case.name);render(case.name,case.width,case.height) end
 load('native_forward')
 t:SetSetting('timelineResolutionWidth','3840');t:SetSetting('timelineResolutionHeight','2160')
 c:SetActiveTool(c:FindTool('MapRemapper'))
 c:Save(base..'/Native Map Remapper Validation.comp')
 assert(r:GetProjectManager():SaveProject())
end)
f:write(ok and 'SUCCESS\n' or ('ERROR\t'..tostring(err)..'\n'));f:close()
if not ok then error(err) end
