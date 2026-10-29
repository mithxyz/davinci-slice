# DaVinci Slice

Native Fusion tools for **DaVinci Resolve Studio on macOS**, driven by Resolume Advanced Output XML.

| Tool | Use it to |
| --- | --- |
| **Slice Mapper** | Frame clips inside input-map screens using Single, Repeat or Span routing. |
| **Map Remapper** | Convert a complete input-map frame into output packing, or reverse the conversion. |

## Illustrated user guides

- [Slice Mapper user guide](docs/user-guide/slice-mapper.md)
- [Map Remapper user guide](docs/user-guide/map-remapper.md)

Both guides include actual Resolve screenshots and rendered examples. Their images are stored alongside them in `docs/user-guide/screenshots/`.

## Quick installation

Download [Slice Mapper.lua](dist/Slice%20Mapper.lua) and [Map Remapper.lua](dist/Map%20Remapper.lua), then copy them to:

```text
~/Library/Application Support/Blackmagic Design/DaVinci Resolve/Fusion/Scripts/Utility/
```

Open the tools from **Workspace > Scripts**. Use **Add to Fusion** for immediate application, or create a `.drfx` bundle for a reusable Edit-page effect. The utilities need Python 3.9+ to import XML; saved effects render with native Fusion nodes and need no custom render plugin.

## Repository contents

| Folder | Contents |
| --- | --- |
| `src/` | Lua interfaces, Python parsers, preview, update and effect builders. |
| `dist/` | Self-contained Lua utilities and ready-made effect bundles. |
| `docs/user-guide/` | Both Markdown guides and screenshots. |
| `tests/` | Portable automated tests and fixtures. |
| `validation/` | Native compositions, rendered frames and verification reports. |
| `demo/` | Generated test-card video used for native validation. |

Run the automated tests with `python3 -m unittest discover -s tests -v`. Native render evidence was captured in Resolve Studio 21.0.2.4; saved validation compositions can contain machine-specific media paths and may need relinking on another Mac.


For converting a completed frame between Resolume’s input and output layouts, use the separate **Map Remapper** below.

A Resolve Studio Edit-page Fusion effect generated from the **input layout** in a Resolume Advanced Output XML. The native importer creates separately named effects with the screens you choose. Each effect has a screen selector and controls for framing content inside its fixed screen boundary.

## Map Remapper — Input ↔ Output

A separate effect and utility now convert content **already arranged on one map** into the other map. Input → Output crops each selected slice from the composition layout and places it in the selected output Screen’s packing. Output → Input reverses that mapping and places those slices back on the input canvas. Content keeps its relative position within each slice; the effect does not refit an ordinary clip independently into every screen.

For example, DavinciMap’s row of six squares above Main becomes the packed output layout: Main at top left, columns at top right, squares underneath and DJ below. A complete frame from the existing Slice Mapper can feed the new remapper. You can also author content directly against the output layout and reverse it into the input layout for rendering.

1. Open **Workspace → Scripts → Map Remapper** after installing `dist/Map Remapper.lua` in Resolve’s Fusion `Scripts/Utility/` folder.
2. Load a Resolume Advanced Output XML and choose an **Output Screen**. Each output Screen has its own coordinate space. Make one effect/render per Screen; overlapping coordinates on different Screens are never combined.
3. Review the paired **Input map / Output map** previews. Confirm the output width and height. Virtual-device sizes are read from the XML. If a capture device’s XML omits the size, the window proposes the composition size and explicitly asks you to confirm it. The CLI requires `--output-size` in that case.
4. Choose slices using the checkboxes; five are shown per page. **Select all / Select none** affect the chosen Screen. The previews show included slices in colour and excluded slices in grey. Unsupported geometry is disabled with a reason.
5. Choose **Initial direction**. On the Fusion page, **Add to Fusion** inserts the group before MediaOut in one undo step. Alternatively, enter a unique effect name and use **Create Bundle** or **Create & Install** for an Edit-page effect. Bundles are saved in `~/Documents/DaVinci Slice/Remappers/<effect name>/`.
6. Use **Direction** and **Include:** switches in the effect’s Inspector. Direction can change per instance. The group emits the destination map’s canvas size; match the timeline/render size to that destination.

The current release supports axis-aligned scale and translation between rectangles, polygons whose output vertices follow that same mapping (including concave U shapes), and native linear/cubic slice masks including inverted holes. Fractional geometry and mask handles are retained. It validates every stored output contour and mesh rather than accepting the output bounding rectangle alone. XML round-off is allowed up to **0.01 output pixels**. **Perspective, deformed linear/Bezier meshes, independently moved polygon vertices, rotated framing rectangles and output flips are reported as unsupported.** Screen/output colour processing, soft edges, hardware routing and output-device latency are outside this geometric conversion.

Both DavinciMap and all three UltraMelb2026 Screens pass the geometry checks. Ready-made `.drfx`, `.setting` and `.remap.json` files are in `dist/Remappers/`, with separate CENTER, SR and SL effects for Ultra Melbourne. Those Ultra bundles use **3840 × 2160** output canvases, an explicit assumption because the DeckLink XML does not store a pixel size. Confirm that size against your processor setup. The older Slice Mapper utility and effects remain separate.

The generated effects use standard Fusion nodes and embed their geometry; no XML, Python, image asset, service or custom render plugin is needed on render machines. Previews are geometry diagrams. This release has 57 passing automated tests and **10 verified native Resolve renders**: both directions for DavinciMap, Ultra CENTER and Ultra SR; a two-slice subset; an empty selection; and 640 × 360 ↔ 800 × 400 conversion. Native affine Transform nodes preserve fractional placement and avoid the alpha-edge artefacts found during corner-positioner testing. The render checks compare opaque, flat source regions and independently rasterized destination masks, allowing a three-pixel antialiasing edge band. All tested flat source samples match within four 8-bit levels and no alpha escapes that band. The native window was exercised for XML loading, paired previews, Screen switching, paging, all/none selection and direct Fusion insertion. The saved ResMap project contains a **Map Remapper — Validation** timeline with the verified mapper selected in Fusion; its composition and results are in `validation/remapper/`.

Use a full frame in the source map’s coordinate space. With differing source/destination sizes, feed the correct source dimensions upstream in Fusion and use the destination size for the render; a clip that Resolve has already letterboxed/cropped cannot be recovered by stretching its conformed frame. A reverse conversion reconstructs available regions only: overlaps, clipping and resampling can discard information, so a round trip is not guaranteed to be lossless. Changing the XML requires generating a new remapper; Slice Mapper’s **Update Map** does not update this separate effect.

```sh
python3 src/build_map_remapper.py \
  "$HOME/Documents/Resolume Arena/Presets/Advanced Output/DavinciMap.xml" \
  --screen "Screen 1" \
  --name "Map Remapper - DavinciMap - Screen 1"

python3 src/build_map_remapper.py \
  "$HOME/Documents/Resolume Arena/Presets/Advanced Output/UltraMelb2026.xml" \
  --screen CENTER --output-size 3840 2160 --direction reverse \
  --name "Map Remapper - Ultra CENTER reverse"

python3 src/package_remapper.py
python3 validation/verify_remapper_renders.py
```

## Import a map

The **Slice Mapper** script is installed on this Mac:

1. Open **Workspace → Scripts → Slice Mapper**.
2. Click **Browse…** and choose a Resolume Advanced Output XML, or paste its path and click **Load**.
3. Choose **Initial routing**: **Single screen**, **Repeat to checked screens**, or **Span full map**. Enter a unique **Effect name** if saving a reusable Edit effect.
4. Click screens in the map preview to include or exclude them, or use the checkboxes. Coloured screens are included; grey screens are excluded. Preview numbers match the list, and hovering identifies the full name. Clicking a screen also opens its list page. **Previous / Next** show ten screens per page; selections persist across pages. **Select all / Select none** apply to the whole map. Disabled or unsupported slices cannot be selected; hover over an unavailable screen for its reason. Hover over a size to see its position.
5. To apply immediately, open your target clip on the **Fusion page** and click **Add to Fusion**. This inserts a native mapper before MediaOut, selects it in the Fusion Inspector, and needs no bundle installation or restart. For a reusable Edit effect, click **Create & Install**, then **Install** in Resolve's template dialog. **Create Bundle** instead saves files without opening the installer.
6. Find the named effect under **Effects → Toolbox → Effects → Davinci Slice**. If it does not appear, save your project and restart Resolve.

Files are saved in `~/Documents/DaVinci Slice/Maps/<effect name>/`: an installable `.drfx`, a Fusion `.setting`, and a `.layout.json` recording the chosen geometry and source XML hash. Names already generated or installed are refused; use a different name or add `v2` for a revised map. Separate maps coexist in the Effects Library. Existing timeline instances keep their embedded mapping. Use **Update Map** for an existing Fusion mapper; new bundles remain useful for sharing a revised map.

The supplied XML was tested through this window: **Slice Mapper - DavinciMap** is installed with **SQ-1, Col-L1 and Main** on a **3840 × 2160** canvas. The original **Slice Mapper Prototype** remains available.

Polygon slices and circular/curved slice masks are supported. The importer retains linear vertices and cubic Bézier handles as native Fusion vector masks, including concave shapes such as Ultra Melbourne's U. It also respects enabled and inverted slice masks, intersecting them with the slice boundary. Fill/Fit/Stretch frame the content against the input rectangle; the actual polygon or curve then clips it. The controls move the content inside that fixed boundary. They do not warp the content to follow each polygon edge.

**UltraMelb2026.xml** has 14 supported screens: 12 polygon slices and two circular Bézier masks. The availability column identifies each shape and flags any slice clipped at the canvas edge. Displayed coordinates are rounded to three decimal places; saved geometry retains the XML precision. **SR 1** starts at X = −0.773 px and clips at the canvas without shifting its content or neighbouring screens.

**Slice Mapper - Ultra Melbourne 2026** is installed with all 14 screens. To use another complex map, open the same importer, load its XML, choose a new effect name and select its screens. Previously generated effects need to be generated again under a new name to gain the new mask support.

The importer currently targets **macOS Resolve Studio** and uses `/usr/bin/python3` (Python 3.9+) for local XML processing. It needs no third-party Python packages or Resolve Python scripting module. The native window and Python helper are bundled in one Lua script; no network service is used. A different interpreter can be selected with the `DAVINCI_SLICE_PYTHON` environment variable inherited by Resolve. Installed effects render with standard Fusion nodes and do not need Python, the importer, XML or PNG.

The preview is generated directly from XML, so a separate input-map PNG is not required. It preserves the canvas aspect ratio and respects polygon boundaries, circular masks and inverted holes. The preview and checkboxes share one selection; empty gaps do not toggle a screen. Overlapping slices select the topmost drawn screen; use the list to select a screen underneath. Very small screens use a number badge instead of their full name. The preview is a selection aid; generated effects retain the original vector precision. Preview images are temporary and removed when the window closes.

Use **+ / −** or the **mouse wheel** to zoom from **100% to 1600%**. Wheel zoom keeps the point under the pointer fixed until the view reaches an edge. Pan with a **middle/right-button drag**, or enable **Pan** and drag with the left button. **Fit** restores the complete centred map. Pan is bounded and becomes useful after zooming in. The thumbnail is rendered again at the new view; zoom/pan do not change screen coordinates, destinations or exported geometry.

## One source feeding multiple screens

Choose a routing mode in the importer before creating or applying a map. You can change it later in the mapper's Inspector:

| Routing | Result |
| --- | --- |
| Single screen | The Screen dropdown chooses one destination. Destination checkboxes are ignored. |
| Repeat to checked screens | A separate copy of the same source is fitted into every enabled **Send to:** destination. Each screen uses its own bounding rectangle. |
| Span full map | The source is fitted to the complete map canvas. Enabled destinations reveal their corresponding regions of that continuous image. Disabling a destination does not reframe the others. |

Every screen included at import gets a **Send to:** checkbox, initially on. Fill/Fit/Stretch, Zoom, offsets, rotation and flips apply to the shared source; Repeat evaluates those controls in each screen's local rectangle, while Span evaluates them in full-map coordinates. No enabled destinations produces transparent output in either multi-screen mode. This uses one source clip on one track and one mapper. Additional tracks are useful only for independent content.

Existing installed effects keep their original embedded graph. Generate/apply a new map to gain routing controls; reinstalling does not upgrade effects already on clips. The new **Slice Mapper - Ultra Melbourne Multi** bundle starts in Repeat mode with all 14 destinations available.

## Update a map on an existing clip

1. Open the clip on the **Fusion page**. Select its **SliceMapper** group (the group created by **Add to Fusion**). If the composition contains only one managed mapper, it can be found automatically; with several mappers, select exactly one.
2. Open **Workspace → Scripts → Slice Mapper**.
3. Browse to the revised Resolume Advanced Output XML and click **Load**. Review the preview and choose the screens to keep available. Zoom and pan for small slices.
4. Click **Update Map**. The selected clip's mapper receives the revised geometry. Its Screen, Scaling, Zoom, offsets, rotation, flips, Routing and existing Send controls retain their values and complete native animation curves, including curve handles and interpolation flags.
5. Close the window and scrub the clip. Use **Edit → Undo** on the Fusion page to undo **Update Slice Mapper**, or Redo to restore it.

Routing is **per clip/effect instance**, and can be changed at any time. On the Fusion page select the mapper and use **Inspector → Controls → Routing**. For a generated Edit-page effect select its clip and use **Inspector → Effects → Fusion → Controls → Routing**. One clip can be Single, another Repeat and another Span using the same saved map effect. Their destination checkboxes and keyframes are independent. **Initial routing** in the importer only sets the starting value for newly created mapper instances; it does not change existing clips and is ignored by Update Map.

Updates match screens by Resolume ID first, then by unique name. Older Add to Fusion mappers without saved IDs are adopted by name on their first update. Retaining the old names for that first update allows them to match. Reordering XML screens does not change the old Screen dropdown indices or retarget selector animation. A renamed screen with the same ID stays in its original slot. Unmatched or excluded old screens remain labelled **Missing: …** and render nothing, retaining their animation slots in case they return later. New screens are appended with **Send to** off; enable them in the clip's Inspector for Repeat/Span, or select them in Single mode. All-new initial imports still start with Send controls on.

The importer selection controls which screen geometry is available to the updated instance; the clip's **Send to** controls choose which available destinations receive this source. Update Map preserves the latter, including animation. It reports matched, renamed, added and missing screens after updating. Other clips and saved Effects Library templates are unchanged. It is an explicit snapshot update: XML edits do not change clips until you load and update them.

**Current scope:** Update Map works on native Fusion groups created with Add to Fusion. Resolve's scripting interface does not expose arbitrary Edit Effects Library instances for this update operation. Existing Edit effects still support independent per-clip routing; to use Update Map, use the Add to Fusion workflow on a clean clip. Do not stack an Edit-page mapper and a Fusion mapper on the same source. For a changed canvas size, set the timeline to the new XML dimensions; offsets keep their numeric pixel values, so review the framing. The updater regenerates internal geometry nodes; it preserves the published controls rather than manual changes inside the generated group.

## Immediate mapping and workflow options

**Add to Fusion is the shortest working path:** open a clip in Fusion → load XML → pick screens and Routing → Add to Fusion. It operates on the open Fusion composition with one connected MediaOut, keeps the entire upstream graph, and groups the insertion as **Undo: Add Slice Mapper**. An existing managed mapper immediately before MediaOut is detected so repeated clicks do not silently stack mappings. Use **Update Map** to revise a mapper already added to this clip while retaining its published framing and animation.

Use a clean source clip, or remove an older Edit-page Slice Mapper effect first, so the clip is not mapped twice.

The chosen map is embedded in the clip's composition. It renders without the XML, importer, helper process or an installed custom plugin. This is immediate application of a snapshot: changing the XML or preview afterwards does not automatically change existing clips. Framing and routing controls for this path are on the **Fusion Inspector**. Saved `.drfx` effects instead expose controls in the **Edit Effects Inspector**.

| Approach | Best use | Trade-off |
| --- | --- | --- |
| Add native Fusion nodes directly — implemented | Trying maps and creating content immediately | Open the clip in Fusion; use Update Map for revised geometry. |
| Named Edit effects — implemented | Reusing and sharing a show map | Bundle install and sometimes a Resolve restart; existing instances retain their mapping. |
| Update Map — implemented | Revising a show while preserving clip framing | Updates the selected Fusion mapper; missing screens retain non-rendering slots and new screens start off. |
| A custom Fuse/OpenFX mapper with map profiles — longer-term option | A polished universal effect for wider distribution | A custom runtime/plugin must be packaged and maintained on render machines; more engineering than the native-node route. |

The direct path uses Fusion's documented `Composition.Paste` and image-input connections, and has been proven in renders on this Mac. The installed Resolve SDK exposes Fusion composition operations; no documented method for attaching an arbitrary Edit-page Fusion effect was found in that SDK. The practical immediate path therefore targets the Fusion composition. See the [Blackmagic Fusion scripting reference](https://documents.blackmagicdesign.com/UserManuals/Fusion8_Scripting_Guide.pdf) and the installed `Developer/Scripting/README.txt` for the APIs reviewed. A separate network service is unnecessary for the current workflow.

## Use in Resolve

The prototype is already installed on this Mac. The **ResMap** project contains a **Slice Mapper — Edit Demo** timeline with the effect applied to a test clip.

1. Use a **3840 × 2160, square-pixel timeline**.
2. Open **Effects → Toolbox → Effects → Davinci Slice**, or search for **Slice Mapper Prototype** while the Effects category is selected.
3. Drag the effect onto a video or image clip.
4. Select the clip and open **Inspector → Effects → Fusion → Controls**.
5. Choose a screen, then frame the content using the controls below. Imported effects list only the screens included when they were created.
6. Use Repeat or Span to feed multiple screens from this clip. Use separate tracks for independent sources. The area outside the enabled destinations is transparent.

| Control | Behavior |
| --- | --- |
| Screen | Selects the destination input-map rectangle. |
| Routing / Send to | Chooses single-screen, repeated or continuous full-map content and enables destinations. |
| Scaling: Fill | Preserves the source aspect ratio and crops to fill the screen. |
| Scaling: Fit | Preserves the source aspect ratio and shows the whole frame; unused space is transparent. |
| Scaling: Stretch | Fits both dimensions independently, changing the source aspect ratio. |
| Zoom | Multiplies the chosen scaling; 1 is the default framing. |
| Offset X | Moves content in destination-screen pixels; positive moves right. |
| Offset Y | Moves content in destination-screen pixels; positive moves up. |
| Rotation | Rotates the content inside the fixed boundary, in degrees. |
| Flip Horizontal / Vertical | Mirrors the content inside the screen. |

Use the effect's framing controls instead of the clip's Video Transform controls: a later clip transform can move the entire mapped result. Place image-expanding effects such as blur/glow before the mapper if they must remain inside the screen boundary. Large offsets, rotation or zooming out can expose transparent areas.

The screen selection and framing controls can be keyframed using the Inspector diamonds. A screen change jumps between destinations; it is not a spatial transition between screens.

## Install on another Mac

Copy [dist/Slice Mapper.lua](dist/Slice%20Mapper.lua) to `~/Library/Application Support/Blackmagic Design/DaVinci Resolve/Fusion/Scripts/Utility/` (create the folder if needed). Open Workspace → Scripts to launch it. The installed copy is self-contained; the `src` directory is not required.

To share a generated effect, copy its `.drfx`, double-click it and choose Install in Resolve. The original **dist/Slice Mapper Prototype.drfx** can also be installed this way. If it does not appear, save your project and restart Resolve; this was required on Studio 21.0.2.4 during testing. Avoid installing the loose `.setting` as well as the bundle, which can create duplicate entries.

The effect uses standard Fusion nodes only. There is no external service, runtime Python dependency, Fuse or OpenFX binary. The XML and PNG are not needed to render an installed instance.

## Regenerate from XML

Python 3.9+ and the standard library are sufficient to build the effect:

```sh
python3 src/build_slice_mapper.py \
  "$HOME/Documents/Resolume Arena/Presets/Advanced Output/DavinciMap.xml" \
  --screens Main SQ-1 Col-L1 \
  --name "Slice Mapper - Melbourne 2026" \
  --routing repeat
```

Outputs go to `dist/` by default: a `.setting`, installable `.drfx` bundle and named `.layout.json`. Use `--output` for a different folder. Existing files are refused unless the CLI's explicit `--overwrite` option is used. Prefer a new name for a changed map, since existing timeline instances embed their own settings.

The parser preserves fractional coordinates and rejects missing, disabled, duplicate or unsupported selected slices. All 14 rectangles in DavinciMap.xml are available in the importer; Main, SQ-1 and Col-L1 have been tested in Resolve. To rebuild the bundled importer after editing its source, run `python3 src/package_importer.py` and replace the installed Lua script.

## Prototype limits

- Input-map authoring only. Output packing, output warping, soft edges, output color adjustments and live Resolume routing are not reproduced.
- Supports axis-aligned framing rectangles, closed polygon slices, and slice masks with linear or cubic Bézier segments (including circles and ellipses). Rotated framing rectangles, open contours, unknown segment types and multiple contours within one mask are rejected with a reason. Separate enabled masks intersect; compound paths with several contours in one mask are not supported yet.
- Fixed canvas size from the XML; use a matching square-pixel timeline. DavinciMap.xml requires UHD. Source footage is assumed to have square pixels. If Resolve has already letterboxed or cropped the incoming image, the effect sees that conformed image.
- Span uses input-map canvas coordinates. It does not reconstruct a separate physical stage arrangement from a packed map.

## Verification

Tested in **DaVinci Resolve Studio 21.0.2.4 on macOS**. Seven full-resolution RGBA frames were rendered through Resolve for Main Fill, square Fill/Fit, column Fill/Fit/Stretch, and square framing with zoom, offsets, rotation and both flips. `validation/verify_renders.py` checks their actual alpha bounds and verifies zero alpha outside the selected screen. SQ-1's XML X position of 383.5 is retained, including fractional edge coverage.

```sh
python3 -m unittest discover -s tests -v
python3 validation/verify_renders.py
python3 validation/verify_complex_renders.py
python3 validation/verify_routing_renders.py
python3 validation/verify_update_renders.py
```

Render verification uses Pillow and NumPy. The installed Edit effect was also applied to a clean clip and its Screen selector and Zoom control were exercised in the native Inspector. The `Slice Mapper — Prototype` timeline holds the graph used for the render tests; `Slice Mapper — Edit Demo` demonstrates the installed Edit effect.

The importer has 47 automated tests covering layout parsing, selection, named bundle generation, collisions, changed XML, invalid names, XML entity rejection, the packaged backend, polygon vertices, Bézier handles, mixed segments, mask inversion, disabled masks, malformed contours, routing and preview hit testing. Preview tests cover circles, concavity, empty space, inverted holes, overlap order, portrait letterboxing, highlight changes, PNG validity, zoom/pan transforms, bounds and stale XML. The native window was exercised in Resolve to load all 14 rectangular slices, select a three-screen subset across pages, create a named bundle and install it. Native preview event checks also verified synchronized highlights/checkboxes, page navigation from a map hit, no selection change in the U-shaped gap, wheel zoom, a pan drag, circle selection after zoom/pan, and Fit preserving the current selection. Temporary verification controls were removed from the installed importer. The original prototype bundle is unchanged.

All 14 Ultra Melbourne screens were rendered through Resolve as separate full-resolution RGBA frames. `verify_complex_renders.py` compares each frame against an independent rasterization of the XML contour, allowing a three-pixel edge band for differences in antialiasing and rasterization. It checks opaque interior coverage, zero alpha beyond that band, both circle centres/corners and the hollow U. Two additional renders verify curved-screen framing and an inverted circular hole contained by its rectangle. `validation/complex/all_screens_preview.png` combines the 14 actual screen renders for inspection; it is not a dedicated multi-screen mode in the effect. The full native graph is saved as `validation/complex/Ultra Melbourne Validation.comp`.

The importer also pasted the new mapper into a clean clip's Fusion composition without a bundle install. Eight actual renders verify single-screen output, Repeat/Span on all screens, Repeat/Span on two enabled destinations, an empty selection, single-screen output with destination toggles off, and multi-screen framing. Repeat and Span produce visibly different content while keeping alpha within the intended masks. The native graph and rendered previews are in `validation/live_apply/`.

Update validation uses a dedicated **Slice Mapper — Update Validation** timeline. Eight original frames and eight same-map update frames are pixel-identical. A further eight reordered/renamed/added-screen frames remain pixel-identical, with new destinations off. Eight changed-geometry frames confirm a removed screen is transparent and a circular mask moves exactly 50 px right and 100 px down; translated edge coverage differs by at most one 8-bit level. The native validation also compares all 21 animated control curves and two static controls, tests Undo/Redo, deliberately fails an update to verify tool/connection rollback, and changes routing on a duplicate instance to verify independence. Results are saved in `validation/update/report.json` and `native_report.setting`.
