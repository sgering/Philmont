# Project Organization

This project keeps the active generator scripts at the repository root because their defaults currently point to `References/` and `DailyGuide/` relative to the root. That keeps the working commands stable.

## Active Folders

- `DailyGuide/`: source guide content, remaining day folders, daily Markdown source files, and generated map outputs.
- `DailyGuide/DailyMaps/`: current generated daily map PDFs and the combined map PDF.
- `References/GIS/`: shapefiles, KML/KMZ reference layers, DEM data, and QGIS projects.
- `References/USGS/`: USGS source maps, geologic references, and derived topographic rasters.
- `References/Philmont/`: Philmont-specific PDFs, trek map extracts, georeferencing files, and overview profile image.
- `References/Astronomy/`, `References/DeepSearch/`, and `References/Riddles/`: supporting reference material.
- `docs/`: repository-level documentation.

## Local-Only Folders

- `.venv/`: local Python virtual environment.
- `.vscode/`: editor settings.
- `qgis_rasterio_nodeps/`: local rasterio dependency folder for QGIS Python.
- `qgis_pyextras/`: local extra dependencies, currently used for `pypdf`.
- `scratch/`: temporary files, focused test inputs, and preview outputs.
- `archive/`: old generated outputs or uncertain files preserved outside the active workflow.

## Files To Review Before Committing

- Large PDFs and raster files under `References/`.
- Generated PDFs under `DailyGuide/DailyMaps/`.
- Any archived outputs under `archive/`.

## Known Naming Issues

The following filenames appear to have typos but are left unchanged because scripts or existing content may depend on them:

- `DailyGuide/dailiy-hiking-stats.md`
- `DailyGuide/daily-scout-reflrection.md`
- `DailyGuide/scout-lay-by-day.md`
- `References/Philmont/Activity-Descritptions.pdf`
