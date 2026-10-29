-- Run only on the dedicated test timeline after the three importer updates.
local fusion=fusion or fu or bmd.scriptapp('Fusion')
local root='/Users/mattosborne/M-projects/Claude/Davinci Slice/'
assert(bmd.scriptapp('Resolve'):GetProjectManager():GetCurrentProject():GetCurrentTimeline():GetName()=='Slice Mapper — Update Validation')
local c=fusion:GetCurrentComp()
local baseline=bmd.readfile(root..'validation/update/saved.setting')
local function controller(saved,name)
    local g=saved.Tools[name]
    return g.Tools[g.Inputs.Screen.SourceOp]
end
local function equal(a,b)
    if type(a)~=type(b) then return false end
    if type(a)~='table' then return a==b end
    for k,v in pairs(a) do if not equal(v,b[k]) then return false end end
    for k in pairs(b) do if a[k]==nil then return false end end
    return true
end
local old=controller(baseline,'SliceMapper')
local function current_mapper()
 local found
 for _,t in pairs(c:GetToolList(false,'GroupOperator')) do
  if t:GetData('DavinciSlice.SourceHash') then assert(not found); found=t end
 end
 return assert(found)
end
local mapper=current_mapper()
local saved=mapper:SaveSettings()
assert(bmd.writefile(root..'validation/update/after_moved.setting',saved))
-- Compare both files through the same writer precision. Live-to-live transfer
-- is checked exactly by update_map before it removes the previous group.
saved=bmd.readfile(root..'validation/update/after_moved.setting')
local now=controller(saved,mapper:GetAttrs().TOOLS_Name)
local curves,values=0,0
local ids={'DestinationMode'}
for i=1,8 do ids[#ids+1]='NumberIn'..i end
for i=1,14 do ids[#ids+1]='Send'..i end
for _,id in ipairs(ids) do
    local a,b=old.Inputs[id],now.Inputs[id]
    if a and a.SourceOp then
        assert(equal(baseline.Tools[a.SourceOp].KeyFrames,saved.Tools[b.SourceOp].KeyFrames),'Curve changed: '..id)
        curves=curves+1
    else
        local av=a and a.Value
        if av==nil then av=old.UserControls[id].INP_Default end
        local bv=b and b.Value
        if bv==nil then bv=now.UserControls[id].INP_Default end
        assert(equal(av,bv),'Value changed: '..id)
        values=values+1
    end
end
assert(#now.UserControls.NumberIn1==15)
assert(now.UserControls.NumberIn1[2].CCS_AddString=='Missing: US')
assert(now.UserControls.NumberIn1[9].CCS_AddString=='SR RING')
assert(now.Inputs.Send15.Value==0)
local hash=mapper:GetData('DavinciSlice.SourceHash')
c:Undo(1)
local undone=current_mapper()
assert(undone:GetData('DavinciSlice.SourceHash')~=hash,'Undo did not restore map metadata')
local undoneControls=controller(undone:SaveSettings(),undone:GetAttrs().TOOLS_Name)
assert(undoneControls.UserControls.NumberIn1[2].CCS_AddString=='US','Undo did not restore removed screen')
c:Redo(1)
assert(current_mapper():GetData('DavinciSlice.SourceHash')==hash,'Redo did not restore updated map')
-- Load the implementation under test in this script's lexical scope.
--__UPDATE_MAP__
local context=update_target()
local meta=bmd.readfile(root..'validation/update/failure_response.setting')
local bad,n=meta.setting:gsub('MainInput1 = InstanceInput { SourceOp = "SourceResize"','MainInput1 = InstanceInput { SourceOp = "Nonexistent"',1)
assert(n==1)
local before={}
for _,t in pairs(c:GetToolList(false)) do before[t:GetAttrs().TOOLI_ID]=true end
local priorOutput=c:FindTool('MediaOut1'):FindMainInput(1):GetConnectedOutput()
local ok,err=pcall(update_map,context,bad,meta,root..'validation/update/failure_temp.setting')
assert(not ok,'Broken generated mapping unexpectedly succeeded')
local after={}
for _,t in pairs(c:GetToolList(false)) do after[t:GetAttrs().TOOLI_ID]=true end
assert(equal(before,after),'Failure rollback left extra tools or removed existing tools')
assert(c:FindTool('MediaOut1'):FindMainInput(1):GetConnectedOutput()==priorOutput,'Failure changed output connection')
assert(current_mapper():GetData('DavinciSlice.SourceHash')==hash)
-- Verify that two instances do not share a routing property or spline.
local original=current_mapper()
local originalRouting=original:GetInput('DestinationMode',0)
local idsBefore={}
for _,t in pairs(c:GetToolList(false)) do idsBefore[t:GetAttrs().TOOLI_ID]=true end
assert(c:Paste(original:SaveSettings()))
local duplicate
for _,t in pairs(c:GetToolList(false,'GroupOperator')) do
 if not idsBefore[t:GetAttrs().TOOLI_ID] then duplicate=t end
end
assert(duplicate)
duplicate:SetInput('DestinationMode',2,0)
assert(duplicate:GetInput('DestinationMode',0)==2)
assert(original:GetInput('DestinationMode',0)==originalRouting,'Routing leaked between instances')
for _,t in pairs(c:GetToolList(false)) do
 pcall(function() if not idsBefore[t:GetAttrs().TOOLI_ID] then t:Delete() end end)
end
c:SetActiveTool(original)
assert(bmd.writefile(root..'validation/update/native_report.setting',{
    curves_preserved=curves,static_values_preserved=values,undo_verified=true,redo_verified=true,
    renamed_screen_slot_preserved=true,missing_screen_slot_preserved=true,new_screen_off=true,
    per_instance_routing_verified=true,rollback_verified=true,rollback_error=tostring(err)}))
print('[Slice Mapper] Native validation passed: '..curves..' complete animation curves; '..values..' static controls; Undo/Redo and failure rollback.')
