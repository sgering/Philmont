# Cleanup Report

## What Changed

- Added `README.md` with project purpose, layout, requirements, and generation commands.
- Added `.gitignore` for Python caches, local QGIS dependency folders, editor state, scratch files, archives, and generated PDFs.
- Added `docs/PROJECT_STRUCTURE.md` with a concise map of active, local-only, and review-before-commit folders.
- Moved obvious scratch/test artifacts out of the active project root and guide folders.
- Removed the root `__pycache__/` directory.

## Second Cleanup Pass

- Updated documentation for the current `References/Philmont/` folder.
- Moved daily Markdown source files from `DailyGuide/DailyMaps/` back to `DailyGuide/`, where the generators expect them.
- Moved generated map PDFs from `DailyGuide/` back to `DailyGuide/DailyMaps/`.
- Moved loose `Day6_image.png` to `archive/loose-assets/Day6_image.png`.
- Updated `generate_daily_maps.py` so the combined overview profile default points to `References/Philmont/Trek_12-15_elevation_profile.png`.
- Removed the regenerated root `__pycache__/` directory.

## Files And Folders Moved

| From | To | Reason |
| --- | --- | --- |
| `.tmp_*` | `scratch/temp-extracts/` | Temporary extracted text snippets |
| `DailySegments_Day2Only/` | `scratch/test-inputs/DailySegments_Day2Only/` | Focused test shapefile input |
| `DailyGuide/DailyMaps_panel_fill_test/` | `scratch/generated-map-tests/DailyMaps_panel_fill_test/` | Generated verification PDFs/previews |
| `DailyGuide/DailyMaps_panel_test/` | `scratch/generated-map-tests/DailyMaps_panel_test/` | Generated verification PDFs/previews |
| `DailyGuide/DailyMaps_original/` | `archive/generated-maps-original/DailyMaps_original/` | Older generated map PDFs preserved for comparison |

## Intentionally Left In Place

- `generate_daily_maps.py` and `generate_daily_info_sheets.py` remain at the repository root because their defaults use root-relative paths.
- `DailyGuide/` remains the active source/output area for guide content and final generated maps.
- `References/` remains the active source data area for GIS, rasters, PDFs, and research inputs.
- `qgis_rasterio_nodeps/` and `qgis_pyextras/` remain in place for local execution but are ignored by Git.
- Existing typo-like filenames remain unchanged because scripts or current workflow may depend on them.

## Validation

- Ran a syntax check with QGIS Python:

```powershell
python -m py_compile generate_daily_maps.py generate_daily_info_sheets.py
```

- No active generator paths were changed, so a sample regeneration was not required.

## Review Before Commit

- Decide whether large reference PDFs, TIF rasters, and generated map PDFs should be committed or distributed outside GitHub.
- Review `References/` for third-party/copyrighted documents before publishing.
- Review ignored generated outputs in `DailyGuide/DailyMaps/` if you want final PDFs tracked in Git instead of generated locally.
