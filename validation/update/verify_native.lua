local passed,problem=pcall(function()
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
-- Rebuild geometry while transferring complete native control/modifier settings.
local function update_target()
    local resolve=bmd.scriptapp('Resolve')
    if not resolve or resolve:GetCurrentPage()~='fusion' then
        error('Open the clip on the Fusion page and select its Slice Mapper group first.')
    end
    local comp=fusion:GetCurrentComp()
    if not comp then error('No open Fusion composition.') end
    local selected,all={},{}
    for _,tool in pairs(comp:GetToolList(false,'GroupOperator')) do
        if tool:GetData('DavinciSlice.SourceHash') then all[#all+1]=tool end
    end
    for _,tool in pairs(comp:GetToolList(true,'GroupOperator')) do
        if tool:GetData('DavinciSlice.SourceHash') then selected[#selected+1]=tool end
    end
    local mapper
    if #selected==1 then mapper=selected[1]
    elseif #selected==0 and #all==1 then mapper=all[1]
    else error('Select one Slice Mapper group created with Add to Fusion. Update Map targets Fusion groups; Edit Effects Library instances are not accessible here.') end
    local name=mapper:GetAttrs().TOOLS_Name
    local saved=mapper:SaveSettings()
    local group=saved and saved.Tools and saved.Tools[name]
    local control_name=group and group.Inputs and group.Inputs.Screen and group.Inputs.Screen.SourceOp or 'Controls'
    local controls=group and group.Tools and group.Tools[control_name]
    if not controls or not controls.UserControls or not controls.UserControls.NumberIn1 then
        error('This mapper has an unsupported control layout. The existing mapper was not changed.')
    end
    local previous_json=mapper:GetData('DavinciSlice.Manifest')
    local previous
    if not previous_json then
        previous={legacy=true,slices={}}
        for _,entry in ipairs(controls.UserControls.NumberIn1) do
            if entry.CCS_AddString then previous.slices[#previous.slices+1]={name=entry.CCS_AddString} end
        end
        local canvas=group.Tools.Canvas.Inputs
        previous.width=canvas.Width.Value; previous.height=canvas.Height.Value
    end
    return {comp=comp,mapper=mapper,name=name,saved=saved,group=group,controls=controls,
        previous=previous,previous_json=previous_json}
end

local function same_data(a,b)
    if type(a)~=type(b) then return false end
    if type(a)~='table' then return a==b end
    for k,v in pairs(a) do if not same_data(v,b[k]) then return false end end
    for k in pairs(b) do if a[k]==nil then return false end end
    return true
end

local function update_map(context,setting_text,metadata,temporary_path)
    local comp,old=context.comp,context.mapper
    local source=old:FindMainInput(1):GetConnectedOutput()
    local output=old:FindMainOutput(1)
    if not source or not output then error('The existing mapper needs a connected image input and output.') end
    local consumers=output:GetConnectedInputs() or {}
    local file=assert(io.open(temporary_path,'wb')); file:write(setting_text); file:close()
    local settings=bmd.readfile(temporary_path); os.remove(temporary_path)
    local group=settings and settings.Tools and settings.Tools.SliceMapper
    if not group then error('Could not read the updated mapping settings.') end
    local oldcontrols=context.controls
    local inputs=group.Tools.Controls.Inputs
    local transferred={}
    local function preserve(id)
        if not group.Tools.Controls.UserControls[id] then error('Updated mapper is missing '..id) end
        local value=oldcontrols.Inputs[id]
        if value then inputs[id]=value
        else inputs[id].Value=oldcontrols.UserControls[id].INP_Default end
        transferred[#transferred+1]=id
    end
    for i=1,8 do preserve('NumberIn'..i) end
    -- Older single-destination versions have no routing controls.
    if oldcontrols.UserControls.DestinationMode then preserve('DestinationMode') end
    for i=1,#oldcontrols.UserControls.NumberIn1 do
        if oldcontrols.UserControls['Send'..i] then preserve('Send'..i) end
    end
    -- SaveSettings includes the original spline/modifier definitions, including
    -- Bezier handles and interpolation flags. Paste rewrites colliding names.
    for name,tool in pairs(context.saved.Tools) do
        if name~=context.name then settings.Tools[name]=tool end
    end
    group.ViewInfo=context.group.ViewInfo
    local before={}
    for _,tool in pairs(comp:GetToolList(false)) do before[tool:GetAttrs().TOOLI_ID]=true end
    comp:StartUndo('Update Slice Mapper')
    local mapper
    local ok,message=pcall(function()
        if not comp:Paste(settings) then error('Fusion did not paste the updated map.') end
        for _,tool in pairs(comp:GetToolList(false,'GroupOperator')) do
            if not before[tool:GetAttrs().TOOLI_ID] then
                if mapper then error('More than one mapping group was created.') end
                mapper=tool
            end
        end
        if not mapper then error('Fusion did not create the updated mapping group.') end
        -- Verify complete spline data before replacing any connection.
        local check=mapper:SaveSettings()
        local checked_group=check.Tools[mapper:GetAttrs().TOOLS_Name]
        local c=checked_group.Tools[checked_group.Inputs.Screen.SourceOp]
        for _,id in ipairs(transferred) do
            local a,b=oldcontrols.Inputs[id],c.Inputs[id]
            if a and a.SourceOp then
                local orig=context.saved.Tools[a.SourceOp]
                local copied=b and check.Tools[b.SourceOp]
                if orig and (not copied or not same_data(orig.KeyFrames,copied.KeyFrames)) then
                    error('Could not preserve the animation for '..id..'.')
                end
            elseif a and not same_data(a.Value,b and b.Value) then
                error('Could not preserve the value for '..id..'.')
            end
        end
        if not mapper:FindMainInput(1):ConnectTo(source) then error('Could not reconnect the source.') end
        mapper:SetData('DavinciSlice.SourceHash',metadata.source_sha256)
        mapper:SetData('DavinciSlice.MapName',metadata.map_name)
        mapper:SetData('DavinciSlice.Manifest',metadata.manifest_json)
        mapper.Comments='Slice Mapper: '..metadata.map_name..' | '..metadata.count..' screens | '..metadata.width..' x '..metadata.height
        local newoutput=mapper:FindMainOutput(1)
        if not newoutput then error('Updated map has no image output.') end
        for _,input in pairs(consumers) do
            if not input:ConnectTo(newoutput) then error('Could not reconnect a downstream node.') end
        end
        comp:SetActiveTool(mapper)
        -- Keep the original intact until transfer and connection checks pass.
        old:Delete()
    end)
    if not ok then
        for _,input in pairs(consumers) do pcall(function() input:ConnectTo(output) end) end
        for _,tool in pairs(comp:GetToolList(false)) do
            pcall(function() if not before[tool:GetAttrs().TOOLI_ID] then tool:Delete() end end)
        end
    else
        pcall(function() mapper:SetAttrs({TOOLS_Name=context.name}) end)
    end
    comp:EndUndo(ok)
    if not ok then error(message) end
    return mapper:GetAttrs().TOOLS_Name
end

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

end)
bmd.writefile("/Users/mattosborne/M-projects/Claude/Davinci Slice/validation/update/validation_status.setting",{passed=passed,problem=tostring(problem)})
