-- Separate map-to-map workflow. Python is embedded by package_remapper.py.
local PYTHON_BACKEND = --__PYTHON_BACKEND__
local fusion = fusion or fu or bmd.scriptapp('Fusion')
local ui = fusion.UIManager
local dispatcher = bmd.UIDispatcher(ui)
local WINDOW_ID='com.davincislice.MapRemapper'
local existing=ui:FindWindow(WINDOW_ID)
if existing and existing.Visible then existing:Show(); existing:Raise(); return end
--__APPLY_TO_FUSION__
local function shell_quote(s) return "'"..tostring(s):gsub("'","'\\''").."'" end
local function json(v)
    if type(v)=='string' then
        return '"'..v:gsub('[%z\1-\31\\"]',function(c)
            if c=='"' or c=='\\' then return '\\'..c end
            return string.format('\\u%04x',c:byte())
        end)..'"'
    elseif type(v)=='number' or type(v)=='boolean' then return tostring(v)
    elseif type(v)=='table' then
        local fields={}
        if #v>0 then
            for _,x in ipairs(v) do fields[#fields+1]=json(x) end
            return '['..table.concat(fields,',')..']'
        end
        for k,x in pairs(v) do fields[#fields+1]=json(k)..':'..json(x) end
        return '{'..table.concat(fields,',')..'}'
    end
    return 'null'
end
local base=os.tmpname(); os.remove(base)
local helper=base..'.py'
local file=assert(io.open(helper,'wb')); file:write(PYTHON_BACKEND); file:close()
local function service(request)
    local req,res,log=base..'.json',base..'.setting',base..'.log'
    local f=assert(io.open(req,'wb')); f:write(json(request)); f:close()
    local python=os.getenv('DAVINCI_SLICE_PYTHON') or '/usr/bin/python3'
    os.execute(shell_quote(python)..' '..shell_quote(helper)..' '..shell_quote(req)..' '..shell_quote(res)..' >'..shell_quote(log)..' 2>&1')
    local result=bmd.readfile(res)
    if not result then
        local stream=io.open(log,'rb'); local msg=stream and stream:read('*a') or 'Could not start local remapper engine.'
        if stream then stream:close() end
        result={ok=false,error=msg}
    end
    os.remove(req); os.remove(res); os.remove(log)
    return result
end
local rows={Spacing=4,Weight=0}
for i=1,5 do rows[#rows+1]=ui:HGroup{Weight=0,
    ui:CheckBox{ID='Slice'..i,Text='',Enabled=false,Weight=1,Events={Toggled=true}},
    ui:Label{ID='Info'..i,Text='',Weight=2}} end
local win=dispatcher:AddWindow({ID=WINDOW_ID,WindowTitle='Map Remapper',Geometry={10,10,880,700}},
    ui:VGroup{Spacing=6,
        ui:Label{Text='Map Remapper · Input ↔ Output',Weight=0,Font=ui:Font{PointSize=16,Bold=true}},
        ui:HGroup{Weight=0,ui:LineEdit{ID='Path',PlaceholderText='Resolume Advanced Output XML…'},
            ui:Button{ID='Browse',Text='Browse…',Weight=0},ui:Button{ID='Load',Text='Load',Weight=0}},
        ui:HGroup{Weight=0,ui:Label{Text='Output Screen',Weight=0},ui:ComboBox{ID='Screen',Weight=1},
            ui:Label{Text='Output size',Weight=0},ui:LineEdit{ID='Width',Text='3840',MaximumSize={85,24}},
            ui:Label{Text='×',Weight=0},ui:LineEdit{ID='Height',Text='2160',MaximumSize={85,24}},
            ui:Button{ID='Refresh',Text='Refresh previews',Weight=0}},
        ui:Label{ID='MapInfo',Text='Load a map to see its input and output layouts.',WordWrap=true,Weight=0},
        ui:HGroup{Weight=0,
            ui:VGroup{ui:Label{Text='INPUT MAP · composition'},ui:Button{ID='InputPreview',Text='Input map',Flat=true,FixedSize={380,150},IconSize={376,146}}},
            ui:VGroup{ui:Label{Text='OUTPUT MAP · selected Screen'},ui:Button{ID='OutputPreview',Text='Output map',Flat=true,FixedSize={380,150},IconSize={376,146}}}},
        ui:HGroup{Weight=0,ui:Label{Text='Initial direction',Weight=0},ui:ComboBox{ID='Direction',Weight=1},
            ui:Label{Text='Effect name',Weight=0},ui:LineEdit{ID='Name',PlaceholderText='Map Remapper - Show - Screen',MaxLength=100,Weight=2}},
        ui:VGroup(rows),
        ui:HGroup{Weight=0,ui:Button{ID='Previous',Text='Previous',Weight=0},ui:Button{ID='Next',Text='Next',Weight=0},
            ui:Button{ID='All',Text='Select all',Weight=0},ui:Button{ID='None',Text='Select none',Weight=0},ui:Label{ID='Selection',Text='0 slices'}},
        ui:Label{Text='Feed a complete frame laid out on the source map. Direction and slice switches remain editable in the effect Inspector.',WordWrap=true,Weight=0},
        ui:TextEdit{ID='Status',ReadOnly=true,MinimumSize={0,48},MaximumSize={16777215,65},Weight=0},
        ui:HGroup{Weight=0,ui:Button{ID='Close',Text='Close',Weight=0},ui:HGap(0,1),
            ui:Button{ID='Apply',Text='Add to Fusion',Enabled=false,Weight=0},
            ui:Button{ID='Build',Text='Create Bundle',Enabled=false,Weight=0},ui:Button{ID='Install',Text='Create & Install',Enabled=false,Weight=0}}})
local items=win:GetItems()
items.Direction:AddItems({'Input → Output','Output → Input'})
local state={page=1,selected={},files={},revision=0,busy=false}
local function status(s) items.Status.PlainText=s end
local function screen() return state.catalog and state.catalog.screens[items.Screen.CurrentIndex+1] end
local function selected_keys()
    local keys={}
    for _,s in ipairs(screen() and screen().slices or {}) do if s.supported and state.selected[s.key] then keys[#keys+1]=s.key end end
    return keys
end
local function request(action)
    if not state.catalog or not screen() then error('Load a map and choose an output Screen.') end
    if items.Path.Text~=state.path then error('The XML path changed. Click Load first.') end
    local w,h=tonumber(items.Width.Text),tonumber(items.Height.Text)
    if not w or not h or w%1~=0 or h%1~=0 then error('Output dimensions must be whole pixels.') end
    return {action=action,path=state.path,expected_sha256=state.catalog.source_sha256,
        screen_key=screen().key,keys=selected_keys(),output_size={w,h},direction=items.Direction.CurrentIndex,name=items.Name.Text}
end
local function base64(s)
    local alphabet='ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/'
    local out,value,bits={},0,0
    for i=1,#s do local n=alphabet:find(s:sub(i,i),1,true)
        if n then value=value*64+n-1; bits=bits+6
            if bits>=8 then bits=bits-8; out[#out+1]=string.char(math.floor(value/2^bits)%256); value=value%2^bits end
        end
    end
    return table.concat(out)
end
local function preview()
    local ok,result=pcall(function() return service(request('preview')) end)
    if not ok or not result.ok then
        status('Could not preview: '..tostring(ok and result.error or result))
        items.Apply.Enabled=false; items.Build.Enabled=false; items.Install.Enabled=false
        return
    end
    for _,side in ipairs({'Input','Output'}) do
        state.revision=state.revision+1
        local path=base..'_preview_'..state.revision..'.png'
        local f=assert(io.open(path,'wb')); f:write(base64(result[side:lower()..'_png'])); f:close()
        state.files[#state.files+1]=path
        items[side..'Preview'].Text=''; items[side..'Preview'].Icon=ui:Icon{File=path}
    end
    while #state.files>4 do os.remove(table.remove(state.files,1)) end
    local enabled=#selected_keys()>0
    items.Apply.Enabled=enabled; items.Build.Enabled=enabled; items.Install.Enabled=enabled
end
local function show_page()
    state.busy=true
    local slices=screen() and screen().slices or {}
    local pages=math.max(1,math.ceil(#slices/5)); state.page=math.max(1,math.min(pages,state.page))
    for i=1,5 do
        local s=slices[(state.page-1)*5+i]
        items['Slice'..i].Text=s and s.name or ''; items['Slice'..i].Enabled=s and s.supported or false
        items['Slice'..i].Checked=s and state.selected[s.key] or false
        items['Info'..i].Text=s and (s.supported and (s.shape..' · mapped') or s.reason) or ''
        items['Info'..i].ToolTip=s and s.reason or ''
    end
    items.Previous.Enabled=state.page>1; items.Next.Enabled=state.page<pages
    items.Selection.Text=#selected_keys()..' slices selected · page '..state.page..' of '..pages
    state.busy=false
end
local function choose_screen()
    if state.busy or not screen() then return end
    local s=screen(); state.selected={}; state.page=1
    for _,slice in ipairs(s.slices) do state.selected[slice.key]=slice.supported end
    items.Width.Text=tostring(s.width); items.Height.Text=tostring(s.height)
    local name=('Map Remapper - '..state.catalog.name..' - '..s.name):gsub('[<>:"/\\|?*]','-'):sub(1,100)
    items.Name.Text=name
    items.MapInfo.Text='Input: '..state.catalog.width..' × '..state.catalog.height..' · '..s.name..' · '..
        (s.size_explicit and 'Output size read from XML.' or 'XML omits output pixel size. Confirm width and height before building or applying.')
    status('Colour = included. Use the slice checkboxes. Unsupported warps are listed with a reason.')
    show_page(); preview()
end
local function load()
    state.catalog=nil; state.path=nil
    items.Apply.Enabled=false; items.Build.Enabled=false; items.Install.Enabled=false
    items.InputPreview.Icon=ui:Icon{File=''}; items.OutputPreview.Icon=ui:Icon{File=''}
    items.MapInfo.Text='No map loaded.'
    state.busy=true; items.Screen:Clear(); state.busy=false; show_page()
    local path=items.Path.Text:match('^%s*(.-)%s*$'); items.Path.Text=path
    local result=service{action='inspect',path=path}
    if not result.ok then status('Could not load XML: '..tostring(result.error)); return end
    state.catalog=result.catalog; state.path=path; state.busy=true
    local names={}; for _,s in ipairs(result.catalog.screens) do names[#names+1]=s.name end
    items.Screen:AddItems(names); items.Screen.CurrentIndex=0; state.busy=false; choose_screen()
end
local function perform(action,install)
    local ok,result=pcall(function() return service(request(action)) end)
    if not ok or not result.ok then status('Could not create remapper: '..tostring(ok and result.error or result)); return end
    if action=='setting' then
        local success,name=pcall(apply_to_fusion,result.setting,result,base..'_map.setting')
        status(success and ('Added '..name..'. Set render/timeline size to '..result.width..' × '..result.height..'. Undo: Add Map Remapper.') or tostring(name))
    else
        status('Created '..result.bundle..'\nInstall the bundle to use it as an Edit effect. Bundles are saved in Documents/DaVinci Slice/Remappers.')
        if install then state.install=result.bundle; dispatcher:ExitLoop() end
    end
end
win.On[WINDOW_ID].Close=function() dispatcher:ExitLoop() end
win.On.Close.Clicked=function() dispatcher:ExitLoop() end
win.On.Load.Clicked=load; win.On.Path.ReturnPressed=load
win.On.Browse.Clicked=function()
    local path=fusion:RequestFile((os.getenv('HOME') or '')..'/Documents/Resolume Arena/Presets/Advanced Output/','',{FReqS_Filter='XML Files (*.xml)|*.xml',FReqB_Multi=false})
    if path then items.Path.Text=tostring(path); load() end
end
win.On.Screen.CurrentIndexChanged=choose_screen
win.On.Refresh.Clicked=preview
win.On.Previous.Clicked=function() state.page=state.page-1; show_page() end
win.On.Next.Clicked=function() state.page=state.page+1; show_page() end
for i=1,5 do local row=i
    win.On['Slice'..i].Toggled=function()
        if state.busy or not screen() then return end
        local s=screen().slices[(state.page-1)*5+row]
        if s and s.supported then state.selected[s.key]=items['Slice'..row].Checked; show_page(); preview() end
    end
end
local function select_all(value)
    if not screen() then return end
    for _,s in ipairs(screen().slices) do state.selected[s.key]=s.supported and value end
    show_page(); preview()
end
win.On.All.Clicked=function() select_all(true) end
win.On.None.Clicked=function() select_all(false) end
win.On.Apply.Clicked=function() perform('setting',false) end
win.On.Build.Clicked=function() perform('build',false) end
win.On.Install.Clicked=function() perform('build',true) end
status('Choose a Resolume XML. Each effect remaps one output Screen in either direction.')
win:Show(); dispatcher:RunLoop(); win:Hide(); os.remove(helper)
for _,path in ipairs(state.files) do os.remove(path) end
if state.install then os.execute('open '..shell_quote(state.install)..' >/dev/null 2>&1 &') end
