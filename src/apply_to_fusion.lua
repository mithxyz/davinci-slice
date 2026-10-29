-- Insert a self-contained mapper before the single output of an open Fusion comp.
local function apply_to_fusion(setting_text, metadata, temporary_path)
    local resolve=bmd.scriptapp('Resolve')
    if not resolve or resolve:GetCurrentPage()~='fusion' then
        error('Open the target clip on the Fusion page, then click Add to Fusion.')
    end
    local comp=fusion:GetCurrentComp()
    if not comp then error('No open Fusion composition. Open a clip on the Fusion page first.') end
    local outputs=comp:GetToolList(false,'MediaOut')
    local output,count=nil,0
    for _,tool in pairs(outputs) do output=tool; count=count+1 end
    if count~=1 then error('Add to Fusion needs a composition with exactly one MediaOut.') end
    local target=output:FindMainInput(1)
    local source=target and target:GetConnectedOutput()
    if not source then error('Connect your content to MediaOut before adding the mapper.') end
    local upstream=source:GetTool()
    if upstream:GetData('DavinciSlice.SourceHash') then
        error('A Slice Mapper is already connected to MediaOut. Use Update Map to revise it, or use another clip for a new mapper.')
    end
    local file=assert(io.open(temporary_path,'wb')); file:write(setting_text); file:close()
    local settings=bmd.readfile(temporary_path); os.remove(temporary_path)
    if not settings then error('Could not read the generated mapping settings.') end
    local before={}
    for _,tool in pairs(comp:GetToolList(false)) do before[tool:GetAttrs().TOOLI_ID]=true end
    comp:StartUndo('Add Slice Mapper')
    local mapper
    local ok,message=pcall(function()
        if not comp:Paste(settings) then error('Fusion did not paste the mapping group.') end
        for _,tool in pairs(comp:GetToolList(false,'GroupOperator')) do
            if not before[tool:GetAttrs().TOOLI_ID] then
                if mapper then error('More than one mapping group was created.') end
                mapper=tool
            end
        end
        if not mapper then error('Fusion did not create a mapping group.') end
        local input=mapper:FindMainInput(1)
        local out=mapper:FindMainOutput(1)
        if not input or not out then error('The mapping group has no image input or output.') end
        if not input:ConnectTo(source) then error('Could not connect the source to the mapper.') end
        if not target:ConnectTo(out) then error('Could not connect the mapper to MediaOut.') end
        mapper:SetData('DavinciSlice.SourceHash',metadata.source_sha256)
        mapper:SetData('DavinciSlice.MapName',metadata.map_name)
        mapper:SetData('DavinciSlice.Manifest',metadata.manifest_json)
        mapper.Comments='Slice Mapper: '..metadata.map_name..' | '..metadata.count..' screens | '..metadata.width..' x '..metadata.height
        comp:SetActiveTool(mapper)
    end)
    if not ok then
        target:ConnectTo(source)
        -- Roll back only newly pasted tools; preserve every pre-existing tool.
        for _,tool in pairs(comp:GetToolList(false)) do
            pcall(function()
                if not before[tool:GetAttrs().TOOLI_ID] then tool:Delete() end
            end)
        end
    end
    comp:EndUndo(ok)
    if not ok then error(message) end
    return mapper:GetAttrs().TOOLS_Name
end
