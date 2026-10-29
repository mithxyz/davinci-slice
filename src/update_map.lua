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
        for _,tool in pairs(group.Tools) do
            local p=tool.Inputs
            if p and p.TopLeftAlpha and p.Width and p.Height then
                previous.width=p.Width.Value; previous.height=p.Height.Value; break
            end
        end
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
        local imageinput=mapper:FindMainInput(1)
        if not imageinput then error('Updated map has no image input.') end
        if not imageinput:ConnectTo(source) then error('Could not reconnect the source.') end
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
