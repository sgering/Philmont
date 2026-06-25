# Philmont Trek 12-15 Daily Guide

This repository contains the source material and Python tooling used to build a printable daily guide and map set for Philmont Trek 12-15.

The main workflow generates one PDF map page per hiking day, plus a combined map packet. The maps combine daily route shapefiles, USGS topographic raster data, optional hillshade, reference GIS layers, points of interest, daily text panels, and elevation profiles.

## Current Outputs

Generated map PDFs are in:

```text
DailyGuide/DailyMaps/
```

Current map outputs:

```text
DailyGuide/DailyMaps/DailyMaps_combined.pdf
DailyGuide/DailyMaps/Day2_Harlan.pdf
DailyGuide/DailyMaps/Day3_Devils Wash Basin.pdf
DailyGuide/DailyMaps/Day4_Cimarron River.pdf
DailyGuide/DailyMaps/Day5_SantaClaus.pdf
DailyGuide/DailyMaps/Day6_BaldyTown.pdf
DailyGuide/DailyMaps/Day7_BaldyPeak.pdf
DailyGuide/DailyMaps/Day8_PUEBLANO.pdf
DailyGuide/DailyMaps/Day9_Elkhorn.pdf
DailyGuide/DailyMaps/Day10_Ponil.pdf
DailyGuide/DailyMaps/Day11_INDIAN WRITINGS.pdf
DailyGuide/DailyMaps/Day12_SixMileGate.pdf
```

Generated/assembled guidebook artifacts are in:

```text
DailyGuide/Guidebook/
```

## Reference Materials

Reference materials are organized under `References/`:

```text
References/
|-- GIS/
|   |-- DailySegments/                  # Daily route shapefiles
|   |-- DomesticNames_NM_Text/          # Named geographic features / POI source
|   |-- Elevation/                      # DEM used for hillshade and elevation profiles
|   |-- Geologic_Formations/            # Geology GIS source data
|   |-- Geology_Sections/               # Cross-section traces and images
|   |-- trek_12_15_points_shapefile/    # Trek-specific camps and POIs
|   `-- Web_Data_2023/                  # KML/KMZ reference layers
|-- USGS/
|   |-- USGS_I-425_1-prnt_modified.tif  # Main topographic raster used by maps
|   |-- USGS_I-425_1-prnt.pdf           # Original USGS map PDF
|   |-- pp_505.pdf                      # USGS geology reference
|   `-- ofr_22.pdf                      # USGS geology/reference document
|-- Philmont/
|   |-- Trek 12-15.pdf                  # Philmont trek source map
|   |-- Trek 12-15.pdf.points           # Georeferencing points
|   |-- Trek 12-15_modified.tif         # Georeferenced trek raster
|   |-- Trek_12-15_elevation_profile.png
|   |-- Itinerary-Guidebook.pdf
|   |-- 2024-Guidebook-to-Adventure.pdf
|   `-- utm_sites_and_elevations.pdf
|-- Astronomy/                          # Moon, planet, and sky references
|-- DeepSearch/                         # Research PDFs
`-- Riddles/                            # Riddle and joke source PDFs
```

Daily text inputs used by the map generator are in:

```text
DailyGuide/dailiy-hiking-stats.md
DailyGuide/daily-geology.md
DailyGuide/daily-riddle.md
DailyGuide/daily-what-you-will-see.md
DailyGuide/daily-astronomy.md
DailyGuide/daily-scout-skill.md
DailyGuide/daily-clone-wars-quote.md
```

Some filenames intentionally keep their current spelling because the scripts reference them directly, including `dailiy-hiking-stats.md` and `daily-scout-reflrection.md`.

## Script Overview

### `generate_daily_maps.py`

Builds the daily printable map PDFs.

Main responsibilities:

- Reads daily trail segments from `References/GIS/DailySegments/`.
- Reprojects vector and raster inputs to UTM Zone 13N (`EPSG:32613`).
- Clips/warps the USGS topo raster and optional DEM to each daily map area.
- Draws topo, hillshade, geology overlays, reference trails/roads, POIs, camps, and the daily route.
- Adds sidebar panels for map legend, geology key, trail summary, and elevation profile.
- Adds bottom daily text panels for geology, riddle, "Look For This Today", and astronomy.
- Generates one PDF per daily shapefile.
- Optionally combines all daily PDFs into `DailyMaps_combined.pdf`.

Important defaults:

```text
Daily segments:       References/GIS/DailySegments
Topo raster:          References/USGS/USGS_I-425_1-prnt_modified.tif
DEM raster:           References/GIS/Elevation/Elevation_Subset.tif
Reference layers:     References/GIS/Web_Data_2023
POI shapefile:        References/GIS/DomesticNames_NM_Text/Names_Subset.shp
Overview profile:     References/Philmont/Trek_12-15_elevation_profile.png
Output folder:        DailyGuide/DailyMaps
```

### `generate_daily_info_sheets.py`

Builds supplemental daily information sheets from the Markdown source files in `DailyGuide/`.

Main responsibilities:

- Reads day titles, hiking stats, geology notes, voices-from-the-land notes, astronomy notes, fun facts, riddles, and challenges.
- Composes print-ready PDF information sheets.
- Writes outputs to `DailyGuide/DailyInfoSheets/` by default.

## Project Layout

```text
.
|-- generate_daily_maps.py
|-- generate_daily_info_sheets.py
|-- DailyGuide/
|   |-- DailyMaps/          # Current generated map PDFs
|   |-- Guidebook/          # Generated/assembled guidebook artifacts
|   |-- day02-harlan/       # Remaining day-specific content folder
|   `-- daily-*.md          # Daily text source files
|-- References/             # GIS data, rasters, PDFs, and research sources
|-- docs/                   # Cleanup and project-structure notes
|-- scratch/                # Local scratch/test artifacts
`-- archive/                # Preserved old or loose artifacts
```

## Recommended Python Setup

For future flexibility on Windows, use a dedicated conda-forge environment instead of mixing QGIS Python with pip-installed local folders.

Recommended:

```powershell
conda create -n philmont-maps -c conda-forge python=3.12 geopandas rasterio matplotlib pandas numpy shapely pyproj pyogrio pypdf reportlab pyyaml pillow
conda activate philmont-maps
```

The current workspace has also used QGIS Python 3.12 with local helper dependency folders:

```text
qgis_rasterio_nodeps/
qgis_pyextras/
```

Those folders are local environment artifacts and are ignored by Git.

## Generate Maps

From the repository root:

```powershell
python generate_daily_maps.py `
  --daily-segments References/GIS/DailySegments `
  --topo References/USGS/USGS_I-425_1-prnt_modified.tif `
  --dem References/GIS/Elevation/Elevation_Subset.tif `
  --outdir DailyGuide/DailyMaps `
  --buffer 1000 `
  --combined
```

With the current QGIS Python setup, use:

```powershell
$env:PYTHONHOME='C:\Program Files\QGISQT6 3.44.6\apps\Python312'
$env:PYTHONPATH='C:\Philmont\qgis_pyextras;C:\Philmont\qgis_rasterio_nodeps;C:\Program Files\QGISQT6 3.44.6\apps\Python312\Lib;C:\Program Files\QGISQT6 3.44.6\apps\Python312\Lib\site-packages'
$env:PROJ_DATA='C:\Program Files\QGISQT6 3.44.6\share\proj'
$env:GDAL_DATA='C:\Program Files\QGISQT6 3.44.6\share\gdal'
& 'C:\Program Files\QGISQT6 3.44.6\bin\python.exe' generate_daily_maps.py `
  --daily-segments References/GIS/DailySegments `
  --topo References/USGS/USGS_I-425_1-prnt_modified.tif `
  --dem References/GIS/Elevation/Elevation_Subset.tif `
  --outdir DailyGuide/DailyMaps `
  --buffer 1000 `
  --combined
```

## Generate Daily Info Sheets

```powershell
python generate_daily_info_sheets.py --output DailyGuide/DailyInfoSheets
```

## Important Notes

- Keep shapefile sidecar files together (`.shp`, `.shx`, `.dbf`, `.prj`, `.cpg`, etc.).
- Do not rename typo-like source filenames unless you also update script defaults.
- Large generated PDFs and local dependency folders are ignored by default.
- Review third-party reference PDFs and rasters before publishing them to GitHub.
- `scratch/` and `archive/` are local cleanup/review folders and are ignored by Git.
