-- Native Resolve Studio importer. Python backend is embedded by package_importer.py.
local PYTHON_BACKEND = --__PYTHON_BACKEND__
local fusion = fusion or fu or bmd.scriptapp("Fusion")
local ui = fusion.UIManager
local dispatcher = bmd.UIDispatcher(ui)
local WINDOW_ID = "com.davincislice.SliceMapper"
local existing = ui:FindWindow(WINDOW_ID)
if existing and existing.Visible then existing:Show(); existing:Raise(); return end
local PAGE_SIZE = 10
--__APPLY_TO_FUSION__
--__UPDATE_MAP__

local function shell_quote(s)
    return "'" .. tostring(s):gsub("'", "'\\''") .. "'"
end

local function json(value)
    local kind = type(value)
    if kind == "string" then
        return '"' .. value:gsub('[%z\1-\31\\"]', function(c)
            if c == '"' or c == '\\' then return '\\' .. c end
            return string.format('\\u%04x', c:byte())
        end) .. '"'
    elseif kind == "number" or kind == "boolean" then return tostring(value)
    elseif kind == "table" then
        local fields = {}
        if #value > 0 then
            for _, v in ipairs(value) do fields[#fields+1] = json(v) end
            return '[' .. table.concat(fields, ',') .. ']'
        end
        for k, v in pairs(value) do fields[#fields+1] = json(k) .. ':' .. json(v) end
        return '{' .. table.concat(fields, ',') .. '}'
    end
    return 'null'
end

local base = os.tmpname()
local helper = base .. '.py'
local helperFile = assert(io.open(helper, 'wb'))
helperFile:write(PYTHON_BACKEND); helperFile:close()
os.remove(base)

local function service(request)
    local requestPath, responsePath, logPath = base .. '.json', base .. '.setting', base .. '.log'
    local file = assert(io.open(requestPath, 'wb'))
    file:write(json(request)); file:close()
    -- macOS ships the Python launcher used by this local build; no Resolve API
    -- Python module or third-party package is required by the helper.
    local python = os.getenv('DAVINCI_SLICE_PYTHON') or '/usr/bin/python3'
    os.execute(shell_quote(python) .. ' ' .. shell_quote(helper) .. ' ' .. shell_quote(requestPath)
        .. ' ' .. shell_quote(responsePath) .. ' >' .. shell_quote(logPath) .. ' 2>&1')
    local result = bmd.readfile(responsePath)
    if not result then
        local log = io.open(logPath, 'rb')
        local message = log and log:read('*a') or 'Could not start the local import engine.'
        if log then log:close() end
        result = {ok=false, error=message}
    end
    os.remove(requestPath); os.remove(responsePath); os.remove(logPath)
    return result
end

local screenRows = {Spacing=4}
for i=1,PAGE_SIZE do
    screenRows[#screenRows+1] = ui:HGroup{Weight=0,
        ui:CheckBox{ID='Screen'..i, Text='', Enabled=false, Weight=2, Events={Toggled=true}},
        ui:Label{ID='Metrics'..i, Text='', Weight=2},
        ui:Label{ID='Availability'..i, Text='', Weight=3}}
end
local win = dispatcher:AddWindow({ID=WINDOW_ID, WindowTitle='Slice Mapper', Geometry={80,50,1200,740}},
    ui:VGroup({Spacing=8,
        ui:Label{Text='Slice Mapper', Weight=0, Font=ui:Font{PointSize=16, Bold=true}},
        ui:HGroup{Weight=0,
            ui:LineEdit{ID='XMLPath', PlaceholderText='Resolume Advanced Output XML…'},
            ui:Button{ID='BrowseXML', Text='Browse…', Weight=0},
            ui:Button{ID='LoadXML', Text='Load', Weight=0}},
        ui:Label{ID='MapInfo', Text='No map loaded.', WordWrap=true, Weight=0},
        ui:HGroup{Weight=0,
            ui:Label{Text='Effect name', Weight=0},
            ui:LineEdit{ID='EffectName', PlaceholderText='e.g. Slice Mapper - Melbourne 2026', MaxLength=100},
            ui:Label{Text='Initial routing', Weight=0},
            ui:ComboBox{ID='InitialRouting', MinimumSize={190,22}, Weight=0,
                ToolTip='Starting mode for new mapper instances only. Each clip can switch Single, Repeat or Span in its Inspector. Update Map preserves that clip’s routing and animation.'}},
        ui:HGroup{Spacing=16,
            ui:VGroup{Weight=1.3,
                ui:HGroup{Weight=0, Spacing=5,
                    ui:Button{ID='ZoomOut', Text='−', FixedSize={28,22}, Enabled=false},
                    ui:Label{ID='ZoomInfo', Text='100%', FixedSize={44,22}},
                    ui:Button{ID='ZoomIn', Text='+', FixedSize={28,22}, Enabled=false},
                    ui:Button{ID='FitView', Text='Fit', Weight=0, Enabled=false},
                    ui:CheckBox{ID='PanMode', Text='Pan', Enabled=false},
                    ui:Label{Text='Colour = included  •  Grey = excluded'}},
                ui:Button{ID='MapPreview', Text='Load an XML map to preview its screens.', Flat=true,
                    FixedSize={560,350}, IconSize={556,346}, MouseTracking=true,
                    StyleSheet='QPushButton { background-color: #0e1219; border: 1px solid #465166; border-radius: 4px; padding: 0px; }',
                    Events={MousePress=true, MouseMove=true, MouseRelease=true, Wheel=true, Leave=true}},
                ui:Label{ID='PreviewInfo', Text='Click to select. Wheel to zoom. Pan: middle/right drag, or enable Pan.', WordWrap=true,
                    MinimumSize={0,38}, MaximumSize={16777215,44}, Weight=0}},
            ui:VGroup{Weight=1,
                ui:HGroup{Weight=0, ui:Label{Text='Include / screen', Weight=2},
                    ui:Label{Text='Size (px)', Weight=2}, ui:Label{Text='Shape / status', Weight=3}},
                ui:VGroup(screenRows),
                ui:HGroup{Weight=0,
                    ui:Button{ID='Previous', Text='Previous', Enabled=false, Weight=0},
                    ui:Button{ID='Next', Text='Next', Enabled=false, Weight=0},
                    ui:Label{ID='PageInfo', Text='No map loaded'}}}},
        ui:HGroup{Weight=0,
            ui:Button{ID='SelectAll', Text='Select all', Enabled=false, Weight=0},
            ui:Button{ID='SelectNone', Text='Select none', Enabled=false, Weight=0},
            ui:Label{ID='SelectionInfo', Text='0 screens selected'}},
        ui:Label{Text='Use a timeline matching the map’s canvas. Existing clips keep their current mappings.', WordWrap=true, Weight=0},
        ui:Label{Text='Bundles are saved in Documents/DaVinci Slice/Maps.', Weight=0},
        ui:TextEdit{ID='Status', ReadOnly=true, MinimumSize={0,42}, MaximumSize={16777215,56}, Weight=0},
        ui:HGroup{Weight=0,
            ui:Button{ID='CloseButton', Text='Close', Weight=0}, ui:HGap(0,1),
            ui:Button{ID='ApplyFusion', Text='Add to Fusion', Enabled=false, Weight=0,
                ToolTip='Open your clip on the Fusion page. Adds a mapper before MediaOut immediately; controls live on the Fusion page.'},
            ui:Button{ID='UpdateMap', Text='Update Map', Enabled=false, Weight=0,
                ToolTip='Select an existing Slice Mapper group on the Fusion page. Replaces its geometry while keeping framing, routing and animation. New destinations start off. Routing above applies only to new mappers.'},
            ui:Button{ID='CreateBundle', Text='Create Bundle', Enabled=false, Weight=0},
            ui:Button{ID='CreateInstall', Text='Create & Install', Enabled=false, Weight=0}}
    }))
local items = win:GetItems()
items.InitialRouting:AddItems({'Single screen','Repeat to checked screens','Span full map'})
local state = {selected={}, page=1, busy=false, preview_files={}, preview_revision=0, view={zoom=1,cx=.5,cy=.5}}

local function status(text) items.Status.PlainText = text end
local function pixels(value)
    return string.format('%.3f', value):gsub('0+$', ''):gsub('%.$', '')
end
local function selected_names()
    local result = {}
    for _, entry in ipairs(state.catalog and state.catalog.slices or {}) do
        if entry.supported and state.selected[entry.name] then result[#result+1] = entry.name end
    end
    return result
end
local function decode_base64(encoded)
    local alphabet='ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/'
    local result,value,bits={},0,0
    for i=1,#encoded do
        local n=alphabet:find(encoded:sub(i,i),1,true)
        if n then
            value=value*64+n-1; bits=bits+6
            if bits>=8 then
                bits=bits-8; result[#result+1]=string.char(math.floor(value/2^bits)%256)
                value=value%2^bits
            end
        end
    end
    return table.concat(result)
end
local function display_preview(png)
    state.preview_revision=state.preview_revision+1
    local path=base..'_preview_'..state.preview_revision..'.png'
    local file=assert(io.open(path,'wb')); file:write(decode_base64(png)); file:close()
    state.preview_files[#state.preview_files+1]=path
    items.MapPreview.Text=''; items.MapPreview.Icon=ui:Icon{File=path}
    if #state.preview_files>3 then os.remove(table.remove(state.preview_files,1)) end
end
local function refresh_preview()
    if not state.catalog or not state.preview then return end
    local names=selected_names()
    local key=json{names=names,view=state.view}
    if state.preview_key==key then return end
    local response=service{action='preview',path=state.path,names=names,view=state.view,expected_sha256=state.catalog.source_sha256}
    if not response.ok then
        state.preview_error=true
        items.CreateBundle.Enabled=false; items.CreateInstall.Enabled=false
        items.ApplyFusion.Enabled=false
        items.UpdateMap.Enabled=false
        items.PreviewInfo.Text='Preview is out of date. Reload the XML to continue.'
        status('Could not update preview: '..tostring(response.error)); return
    end
    state.preview=response.preview; state.view=response.preview.view
    display_preview(response.preview_png); state.preview_key=json{names=names,view=state.view}
    items.ZoomInfo.Text=math.floor(state.view.zoom*100+.5)..'%'
    items.ZoomOut.Enabled=state.view.zoom>1; items.ZoomIn.Enabled=state.view.zoom<16
end
local function update_selection()
    if state.busy then return end
    local count = #selected_names()
    items.SelectionInfo.Text = count .. (count == 1 and ' screen selected' or ' screens selected')
    items.CreateBundle.Enabled = state.catalog ~= nil and count > 0 and not state.preview_error
    items.CreateInstall.Enabled = state.catalog ~= nil and count > 0 and not state.preview_error
    items.ApplyFusion.Enabled = state.catalog ~= nil and count > 0 and not state.preview_error
    items.UpdateMap.Enabled = state.catalog ~= nil and count > 0 and not state.preview_error
    refresh_preview()
end
local function show_page()
    state.busy=true
    local entries = state.catalog and state.catalog.slices or {}
    local pages = math.max(1, math.ceil(#entries/PAGE_SIZE))
    for i=1,PAGE_SIZE do
        local entry = entries[(state.page-1)*PAGE_SIZE+i]
        local check, metrics, availability = items['Screen'..i], items['Metrics'..i], items['Availability'..i]
        check.Text=entry and (((state.page-1)*PAGE_SIZE+i)..'  '..entry.name) or ''; check.Enabled=entry and entry.supported or false
        check.Checked=entry and state.selected[entry.name] or false
        metrics.Text=entry and entry.width and (pixels(entry.width)..' × '..pixels(entry.height)) or ''
        metrics.ToolTip=entry and entry.width and ('Position: '..pixels(entry.x)..', '..pixels(entry.y)..' px') or ''
        availability.Text=entry and (entry.supported and ((entry.shape or 'Rectangle') .. (entry.warning and ' • clipped' or '')) or 'Unavailable') or ''
        availability.ToolTip=entry and (entry.warning or entry.reason) or ''
        check.ToolTip=entry and (entry.supported and (entry.screen .. (entry.warning and ' • '..entry.warning or '')) or entry.reason) or ''
    end
    items.Previous.Enabled=state.page>1
    items.Next.Enabled=state.page<pages
    items.PageInfo.Text=#entries>0 and ('Page '..state.page..' of '..pages) or 'No map loaded'
    state.busy=false
    update_selection()
end
local function reset_preview()
    state.catalog=nil; state.path=nil; state.selected={}; state.page=1; state.busy=true
    items.MapInfo.Text='No map loaded.'
    items.SelectAll.Enabled=false; items.SelectNone.Enabled=false
    state.preview=nil; state.preview_key=nil; state.preview_error=nil
    state.view={zoom=1,cx=.5,cy=.5}; state.drag=nil
    items.ZoomInfo.Text='100%'; items.ZoomOut.Enabled=false; items.ZoomIn.Enabled=false
    items.FitView.Enabled=false; items.PanMode.Enabled=false
    items.MapPreview.Icon=ui:Icon{File=''}; items.MapPreview.Text='Load an XML map to preview its screens.'
    items.PreviewInfo.Text='Click to select. Wheel to zoom. Pan: middle/right drag, or enable Pan.'
    state.busy=false; show_page()
end
local function load_xml()
    reset_preview()
    local path = items.XMLPath.Text:match('^%s*(.-)%s*$')
    items.XMLPath.Text=path
    local response = service{action='inspect', path=path}
    if not response.ok then status('Could not load XML: ' .. tostring(response.error)); return end
    state.catalog=response.catalog; state.path=path; state.busy=true
    local supported = 0
    for _, entry in ipairs(state.catalog.slices) do
        state.selected[entry.name]=entry.supported
        if entry.supported then supported=supported+1 end
    end
    state.busy=false
    local c = state.catalog
    items.EffectName.Text = response.suggested_name
    state.preview=response.preview; state.view=response.preview.view
    state.preview_key=json{names=selected_names(),view=state.view}
    display_preview(response.preview_png)
    items.ZoomIn.Enabled=true; items.FitView.Enabled=true; items.PanMode.Enabled=true
    items.MapInfo.Text = c.width .. ' × ' .. c.height .. ' canvas  •  ' .. supported .. ' of ' .. #c.slices .. ' slices available'
    items.SelectAll.Enabled=supported>0; items.SelectNone.Enabled=supported>0
    status(supported>0 and 'Click screens in the map or use the checkboxes. Coloured screens will be included in the effect.'
        or 'No supported input geometry found. Hover over an unavailable screen for its reason.')
    show_page()
end
local function in_rect(rect,x,y)
    return x>=rect[1] and x<rect[1]+rect[3] and y>=rect[2] and y<rect[2]+rect[4]
end
local function in_polygon(points,x,y)
    local inside=false
    local a=points[#points]
    for _,b in ipairs(points) do
        if (a[2]>y)~=(b[2]>y) and x<(b[1]-a[1])*(y-a[2])/(b[2]-a[2])+a[1] then inside=not inside end
        a=b
    end
    return inside
end
local function preview_point(pos)
    if not state.preview or not pos then return nil end
    local size=items.MapPreview:Size()
    local w,h=size[1] or size.Width or size.width, size[2] or size.Height or size.height
    local px,py=pos[1] or pos.x or pos.X, pos[2] or pos.y or pos.Y
    if not w or not h or not px or not py then return nil end
    local scale=math.min((w-4)/state.preview.width,(h-4)/state.preview.height)
    if scale<=0 then return nil end
    local x=(px-(w-state.preview.width*scale)/2)/scale
    local y=(py-(h-state.preview.height*scale)/2)/scale
    return x,y
end
local function preview_hit(pos)
    local x,y=preview_point(pos)
    if not x or not in_rect(state.preview.canvas,x,y) then return nil end
    for i=#state.preview.shapes,1,-1 do
        local shape=state.preview.shapes[i]
        local hit=in_rect(shape.rect,x,y)
        if hit then
            for _,c in ipairs(shape.constraints) do
                if in_polygon(c.points,x,y)~=c.inside then hit=false; break end
            end
        end
        if hit then return shape.supported and shape.index or nil end
    end
end
local function zoom_view(factor,pos)
    if not state.preview or state.preview_error then return end
    local zoom=math.min(16,math.max(1,state.view.zoom*factor))
    local x,y=preview_point(pos)
    x,y=x or state.preview.width/2,y or state.preview.height/2
    local c=state.preview.canvas
    local nx,ny=(x-c[1])/c[3],(y-c[2])/c[4]
    local ratio=zoom/state.view.zoom
    state.view={zoom=zoom,cx=math.min(1,math.max(0,nx-(x-state.preview.width/2)/(c[3]*ratio))),
        cy=math.min(1,math.max(0,ny-(y-state.preview.height/2)/(c[4]*ratio)))}
    if zoom==1 then state.view.cx=.5; state.view.cy=.5 end
    refresh_preview()
end
local function pan_view(pos)
    local x,y=preview_point(pos)
    if not x or not state.drag then return end
    local d=state.drag
    state.view.cx=math.min(1,math.max(0,d.cx-(x-d.x)/d.width))
    state.view.cy=math.min(1,math.max(0,d.cy-(y-d.y)/d.height))
    if state.view.zoom==1 then state.view.cx=.5; state.view.cy=.5 end
    refresh_preview()
end
local function describe_preview(index)
    local entry=index and state.catalog.slices[index]
    if entry then
        items.PreviewInfo.Text=index..'  '..entry.name..'  •  '..(state.selected[entry.name] and 'Included' or 'Excluded')..'  •  Click to toggle'
    else items.PreviewInfo.Text='Click to select. Wheel to zoom. Pan: middle/right drag, or enable Pan.' end
end
local function select_all(checked)
    state.busy=true
    for _, entry in ipairs(state.catalog and state.catalog.slices or {}) do
        state.selected[entry.name]=entry.supported and checked
    end
    state.busy=false; show_page()
end
local function create_effect(install)
    if not state.catalog then status('Load an XML map first.'); return end
    if state.path ~= items.XMLPath.Text then status('The XML path changed. Click Load to review it first.'); return end
    local result = service{action='build', path=state.path, name=items.EffectName.Text,
        names=selected_names(), routing=items.InitialRouting.CurrentIndex, expected_sha256=state.catalog.source_sha256}
    if not result.ok then status('Could not create effect: ' .. tostring(result.error)); return end
    status('Created ' .. result.name .. ' with ' .. result.count .. ' screens.\n' .. result.bundle
        .. '\nDouble-click the bundle to install. Restart Resolve if the effect does not appear.')
    print('[Slice Mapper] Created ' .. result.bundle)
    if install then state.install=result.bundle; dispatcher:ExitLoop() end
end
local function add_to_fusion()
    if not state.catalog then status('Load an XML map first.'); return end
    if state.path~=items.XMLPath.Text then status('The XML path changed. Click Load first.'); return end
    local resolve=bmd.scriptapp('Resolve')
    if not resolve or resolve:GetCurrentPage()~='fusion' then
        status('Open your target clip on the Fusion page, then click Add to Fusion. The mapping will be inserted before MediaOut.'); return
    end
    local result=service{action='setting',path=state.path,names=selected_names(),routing=items.InitialRouting.CurrentIndex,expected_sha256=state.catalog.source_sha256}
    if not result.ok then status('Could not prepare mapping: '..tostring(result.error)); return end
    result.source_sha256=state.catalog.source_sha256
    local ok,name=pcall(apply_to_fusion,result.setting,result,base..'_map.setting')
    if not ok then status('Could not add mapping: '..tostring(name)); return end
    status('Added '..result.map_name..' ('..result.count..' screens) as '..name..'. Adjust its controls in the Fusion Inspector. Undo: Add Slice Mapper.')
end
local function update_existing_map()
    if not state.catalog then status('Load an XML map first.'); return end
    if state.path~=items.XMLPath.Text then status('The XML path changed. Click Load first.'); return end
    local ok,context=pcall(update_target)
    if not ok then status('Could not update mapping: '..tostring(context)); return end
    local result=service{action='update',path=state.path,names=selected_names(),
        previous=context.previous,previous_json=context.previous_json,expected_sha256=state.catalog.source_sha256}
    if not result.ok then status('Could not prepare update: '..tostring(result.error)); return end
    result.source_sha256=state.catalog.source_sha256
    local changed,name=pcall(update_map,context,result.setting,result,base..'_update.setting')
    if not changed then status('Could not update mapping: '..tostring(name)); return end
    local r=result.report
    local text='Updated '..name..': '..r.matched..' matched, '..#r.renamed..' renamed, '..#r.added..' new (off), '..#r.removed..' missing. Framing and animation kept. Undo: Update Slice Mapper.'
    if #r.removed>0 then text=text..' Missing: '..table.concat(r.removed,', ')..'.' end
    if r.canvas_changed then text=text..' Canvas changed: set the timeline to '..result.width..' x '..result.height..'; pixel offsets retain their values.' end
    if r.legacy then text=text..' First update matched older screens by name; future updates also use Resolume IDs.' end
    status(text); print('[Slice Mapper] '..text)
end
win.On[WINDOW_ID].Close = function(ev) dispatcher:ExitLoop() end
win.On.CloseButton.Clicked = function(ev) dispatcher:ExitLoop() end
win.On.BrowseXML.Clicked = function(ev)
    local start = (os.getenv('HOME') or '') .. '/Documents/Resolume Arena/Presets/Advanced Output/'
    local path = fusion:RequestFile(start, '', {FReqS_Filter='XML Files (*.xml)|*.xml', FReqB_Multi=false})
    if path then items.XMLPath.Text=tostring(path); load_xml() end
end
win.On.LoadXML.Clicked = load_xml
win.On.XMLPath.ReturnPressed = load_xml
win.On.MapPreview.MousePress=function(ev)
    if state.busy or state.preview_error then return end
    if items.PanMode.Checked or ev.Button==2 or ev.Button==4 or ev.Button=='RightButton' or ev.Button=='MiddleButton' then
        local x,y=preview_point(ev.Pos)
        if x then state.drag={x=x,y=y,cx=state.view.cx,cy=state.view.cy,width=state.preview.canvas[3],height=state.preview.canvas[4]} end
        return
    end
    if ev.Button and ev.Button~=1 and ev.Button~='LeftButton' then return end
    local index=preview_hit(ev.Pos)
    if index then
        local entry=state.catalog.slices[index]
        state.selected[entry.name]=not state.selected[entry.name]
        state.page=math.ceil(index/PAGE_SIZE)
        show_page()
    end
    describe_preview(index)
end
win.On.MapPreview.MouseMove=function(ev)
    if state.drag then pan_view(ev.Pos) else describe_preview(preview_hit(ev.Pos)) end
end
win.On.MapPreview.MouseRelease=function(ev)
    if state.drag then pan_view(ev.Pos); state.drag=nil end
end
win.On.MapPreview.Wheel=function(ev)
    local a=ev.AngleDelta
    local delta=type(a)=='table' and (a[2] or a.y or a.Y) or ev.Delta
    if type(delta)~='number' or delta==0 then
        local p=ev.PixelDelta
        delta=type(p)=='table' and (p[2] or p.y or p.Y) or 0
    end
    if type(delta)=='number' and delta~=0 then zoom_view(1.25^(math.min(480,math.max(-480,delta))/120),ev.Pos) end
end
win.On.MapPreview.Leave=function(ev) if not state.drag then describe_preview(nil) end end
win.On.ZoomIn.Clicked=function(ev) zoom_view(1.5) end
win.On.ZoomOut.Clicked=function(ev) zoom_view(1/1.5) end
win.On.FitView.Clicked=function(ev)
    state.view={zoom=1,cx=.5,cy=.5}; state.drag=nil; refresh_preview()
end
for i=1,PAGE_SIZE do
    local index=i
    win.On['Screen'..i].Toggled=function(ev)
        if state.busy or not state.catalog then return end
        local entry=state.catalog.slices[(state.page-1)*PAGE_SIZE+index]
        if entry and entry.supported then state.selected[entry.name]=items['Screen'..index].Checked end
        update_selection()
    end
end
win.On.Previous.Clicked=function(ev) state.page=state.page-1; show_page() end
win.On.Next.Clicked=function(ev) state.page=state.page+1; show_page() end
win.On.SelectAll.Clicked = function(ev) select_all(true) end
win.On.SelectNone.Clicked = function(ev) select_all(false) end
win.On.CreateBundle.Clicked = function(ev) create_effect(false) end
win.On.CreateInstall.Clicked = function(ev) create_effect(true) end
win.On.ApplyFusion.Clicked = add_to_fusion
win.On.UpdateMap.Clicked = update_existing_map
status('Choose a Resolume Advanced Output XML to begin.')
win:Show(); dispatcher:RunLoop(); win:Hide(); os.remove(helper)
for _,path in ipairs(state.preview_files) do os.remove(path) end
if state.install then
    local ok = os.execute('open ' .. shell_quote(state.install) .. ' >/dev/null 2>&1 &')
    print('[Slice Mapper] Opening Resolve installer: ' .. state.install)
end
