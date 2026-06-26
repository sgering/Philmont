"""
generate_daily_maps.py

Short README (top of file):

This script generates printable 8.5x11 (portrait) PDF map pages — one page
per hiking day — using daily trail segment shapefiles, topo/elevation rasters,
geology overlays, camps and POI shapefiles. It uses GeoPandas, Rasterio,
Shapely, PyProj, Matplotlib, and optionally ReportLab (not required).

Dependencies:
  pip install geopandas rasterio shapely pyproj matplotlib descartes fiona matplotlib-scalebar pandas pypdf

Expected folder structure (relative to workspace root):
  References/GIS/DailySegments/                 (shapefile per day)
  References/GIS/Elevation/                     (optional raster tiles)
  References/GIS/Web_Data_2023/                 (KML/KMZ reference layers)
  References/USGS/USGS_I-425_1-prnt_modified.tif (preferred topo raster)
  References/GIS/trek_12_15_points_shapefile/    (camps & POI shapefile(s))
  References/GIS/DomesticNames_NM_Text/Text/DomesticNames_NM.txt
  DailyGuide/DailyMaps/                          (optional outputs)

Usage:
  python generate_daily_maps.py --daily-segments References/GIS/DailySegments \
      --topo References/USGS/USGS_I-425_1-prnt_modified.tif \
      --outdir DailyGuide/DailyMaps --buffer 1000 --combined

The script will produce one PDF per daily shapefile and (optionally) a
combined PDF named `DailyMaps_combined.pdf` in the output folder.

Notes:
 - Default target CRS is EPSG:32613 (UTM zone 13N). The script will reproject
   vector layers and reproject/warp rasters as necessary.
 - Styling and layout are modular; helper functions are provided so you can
   tweak colors, fonts, and placement.

"""

from pathlib import Path
import argparse
import warnings
import sys
import re
import textwrap

import geopandas as gpd
import numpy as np
import rasterio
from rasterio.warp import calculate_default_transform, reproject, Resampling
from rasterio.io import MemoryFile
from rasterio.mask import mask
from shapely.geometry import box, mapping
from shapely.ops import linemerge, unary_union
import matplotlib.pyplot as plt
import matplotlib.patheffects as PathEffects
from matplotlib.lines import Line2D
from matplotlib.patches import Ellipse, Rectangle, Polygon
from matplotlib.ticker import FuncFormatter, MaxNLocator
from matplotlib.colors import to_rgb
from pyproj import CRS

try:
    from pypdf import PdfReader, PdfWriter
except ImportError:
    PdfReader = None
    PdfWriter = None

import pandas as pd

plt.rcParams.update({
    'pdf.fonttype': 42,
    'ps.fonttype': 42,
    'font.family': 'DejaVu Sans',
    'figure.dpi': 150,
    'savefig.dpi': 300,
})

# Configurable styling constants.
# Adjust PAGE_SIZE and LAYOUT below to change the printable sheet and panel dimensions.
PAGE_SIZE = (11, 8.5)  # inches (width, height) landscape letter
TARGET_CRS = 'EPSG:32613'  # UTM Zone 13N
TOPO_ALPHA = 0.72
HILLSHADE_ALPHA = 0.22
GEOLOGY_ALPHA = 0.30
TRAIL_COLOR = '#111111'
TRAIL_WIDTH = 4.3
DAILY_TRAIL_WIDTH = 1.55
DAILY_TRAIL_CASING_WIDTH = DAILY_TRAIL_WIDTH + 0.65
OVERVIEW_TRAIL_WIDTH = 2.20
OVERVIEW_TRAIL_CASING_WIDTH = OVERVIEW_TRAIL_WIDTH + 1.25
CAMP_MARKER = '^'
CAMP_COLOR = '#111111'
POI_MARKER = 'o'
POI_COLOR = '#456b45'
WATER_COLOR = '#247b9b'
ROAD_COLOR = '#8a6a3d'
REFERENCE_TRAIL_COLOR = '#3f7650'
LABEL_FONTSIZE = 7.2

# NPS/field-guide inspired presentation settings. Keep colors, fonts, padding,
# line widths, marker sizes, and legend spacing centralized here.
STYLE = {
    'page_face': '#f8f5ed',
    'panel_face': '#f2eadb',
    'panel_edge': '#5b5142',
    'panel_rule': '#9b8d78',
    'header_face': '#050606',
    'header_rule': '#bda36c',
    'title_color': '#ffffff',
    'text': '#231f1a',
    'muted_text': '#514a3e',
    'water': WATER_COLOR,
    'road': ROAD_COLOR,
    'trail_ref': REFERENCE_TRAIL_COLOR,
    'geology_overlay': '#b98462',
    'font_family': 'DejaVu Sans',
    'title_size': 16.4,
    'subtitle_size': 8.1,
    'section_size': 7.6,
    'body_size': 6.8,
    'small_size': 6.1,
    'panel_pad': 0.060,
    'panel_lw': 0.72,
    'rule_lw': 0.45,
    'legend_marker_size': 7.7,
    'legend_line_width': 2.35,
}

PANEL_FACE = STYLE['panel_face']
PANEL_EDGE = STYLE['panel_edge']
HEADER_FACE = STYLE['header_face']

LAYOUT = {
    # Normalized figure coordinates. Tweak these for page-level composition.
    'header': [0.000, 0.902, 1.000, 0.098],
    'map': [0.014, 0.205, 0.732, 0.679],
    'sidebar_left': 0.760,
    'sidebar_width': 0.226,
    'legend': [0.760, 0.706, 0.226, 0.178],
    'geology': [0.760, 0.548, 0.226, 0.143],
    'summary': [0.760, 0.337, 0.226, 0.196],
    'profile': [0.760, 0.026, 0.226, 0.296],
    'geology_note': [0.014, 0.026, 0.174, 0.158],
    'riddle': [0.200, 0.026, 0.174, 0.158],
    'look_for_this': [0.386, 0.026, 0.174, 0.158],
    'astronomy': [0.572, 0.026, 0.174, 0.158],
}


def find_raster(path_or_dir: Path):
    """Find a raster file: accept file or search directory for first .tif."""
    p = Path(path_or_dir)
    if p.exists() and p.is_file():
        return p
    if p.exists() and p.is_dir():
        tifs = list(p.glob('*.tif'))
        if tifs:
            return tifs[0]
    return None


def reproject_vector(gdf: gpd.GeoDataFrame, target_crs: str) -> gpd.GeoDataFrame:
    if gdf.crs is None:
        raise ValueError('Input vector CRS is undefined; please provide data with CRS.')
    if str(gdf.crs) == target_crs:
        return gdf
    return gdf.to_crs(target_crs)


def get_buffered_extent(gdf: gpd.GeoDataFrame, buffer_m: float) -> box:
    minx, miny, maxx, maxy = gdf.total_bounds
    return box(minx - buffer_m, miny - buffer_m, maxx + buffer_m, maxy + buffer_m)


def expand_extent_to_panel_aspect(extent_geom, panel_layout, page_size) -> box:
    """Expand an extent so equal-scale map data fills a fixed panel."""
    minx, miny, maxx, maxy = extent_geom.bounds
    width = maxx - minx
    height = maxy - miny
    if width <= 0 or height <= 0:
        return extent_geom

    panel_aspect = (panel_layout[3] * page_size[1]) / (panel_layout[2] * page_size[0])
    data_aspect = height / width
    cx = (minx + maxx) / 2
    cy = (miny + maxy) / 2

    if data_aspect < panel_aspect:
        new_height = width * panel_aspect
        half_height = new_height / 2
        return box(minx, cy - half_height, maxx, cy + half_height)

    new_width = height / panel_aspect
    half_width = new_width / 2
    return box(cx - half_width, miny, cx + half_width, maxy)


def reproject_and_clip_raster(src_path: Path, target_crs: str, clip_geom, nodata=None):
    """Warp raster to target_crs and clip to clip_geom (a GeoJSON-like shape in target_crs).

    Returns: (image_array, transform, crs)
    """
    with rasterio.open(src_path) as src:
        src_crs = src.crs
        if src_crs is None:
            raise ValueError(f'Raster {src_path} has no CRS')

        dst_crs = CRS.from_user_input(target_crs)
        if src_crs != dst_crs:
            transform, width, height = calculate_default_transform(
                src.crs, dst_crs, src.width, src.height, *src.bounds
            )
            profile = src.profile.copy()
            profile.update({
                'crs': dst_crs,
                'transform': transform,
                'width': width,
                'height': height,
            })
            # Reproject into memory
            with MemoryFile() as memfile:
                with memfile.open(**profile) as dst:
                    for i in range(1, src.count + 1):
                        reproject(
                            source=rasterio.band(src, i),
                            destination=rasterio.band(dst, i),
                            src_transform=src.transform,
                            src_crs=src.crs,
                            dst_transform=transform,
                            dst_crs=dst_crs,
                            resampling=Resampling.bilinear,
                        )
                    # mask using shapes in dst_crs (clip_geom must be in target_crs)
                    out_image, out_transform = mask(dst, [mapping(clip_geom)], crop=True, nodata=nodata)
                    out_crs = dst.crs
        else:
            # No reprojection needed
            out_image, out_transform = mask(src, [mapping(clip_geom)], crop=True, nodata=nodata)
            out_crs = src.crs

    return out_image, out_transform, out_crs


def calculate_hillshade(dem_array: np.ndarray, transform, azimuth=315, altitude=45, z_factor=1.0):
    """Compute shaded relief from a DEM array."""
    if dem_array.ndim == 3 and dem_array.shape[0] > 1:
        dem = dem_array[0]
    else:
        dem = dem_array[0] if dem_array.ndim == 3 else dem_array
    dem = dem.astype('float64')
    xres = transform.a
    yres = abs(transform.e)
    dy, dx = np.gradient(dem, yres, xres)
    slope = np.arctan(np.hypot(dx * z_factor, dy * z_factor))
    aspect = np.arctan2(dy, -dx)
    az_rad = np.radians(azimuth)
    alt_rad = np.radians(altitude)
    shaded = np.sin(alt_rad) * np.sin(slope) + np.cos(alt_rad) * np.cos(slope) * np.cos(az_rad - aspect)
    shaded = np.clip(shaded, 0, 1)
    return (shaded * 255).astype(np.uint8)


def soften_rgb_image(img, saturation=0.92, lighten=0.04, contrast=1.03):
    """Mute scanned map rasters so overlays and labels remain print-readable."""
    arr = img.astype('float32')
    scale = 255.0 if np.nanmax(arr) > 1.5 else 1.0
    arr = np.clip(arr / scale, 0, 1)
    gray = np.dot(arr[..., :3], [0.299, 0.587, 0.114])[..., None]
    arr[..., :3] = gray + (arr[..., :3] - gray) * saturation
    arr[..., :3] = (arr[..., :3] - 0.5) * contrast + 0.5
    paper = np.array(to_rgb(STYLE['page_face']), dtype='float32')
    arr[..., :3] = arr[..., :3] * (1 - lighten) + paper * lighten
    return np.clip(arr, 0, 1)


def read_point_layers(points_dir: Path, target_crs: str) -> gpd.GeoDataFrame:
    p = Path(points_dir)
    if not p.exists():
        raise FileNotFoundError(f'Points folder {points_dir} not found')
    shapefiles = list(p.glob('*.shp'))
    if not shapefiles:
        raise FileNotFoundError(f'No shapefiles found in {points_dir}')
    gdfs = []
    for shp in shapefiles:
        try:
            g = gpd.read_file(shp)
            if g.crs is None:
                warnings.warn(f'CRS undefined for {shp}; skipping')
                continue
            g = g.to_crs(target_crs)
            gdfs.append(g)
        except Exception as e:
            warnings.warn(f'Could not read {shp}: {e}')
    if not gdfs:
        return gpd.GeoDataFrame()
    return gpd.GeoDataFrame(pd.concat(gdfs, ignore_index=True)).set_crs(target_crs)


def read_points_of_interest(poi_path: Path, target_crs: str) -> gpd.GeoDataFrame:
    """Read the DomesticNames POI shapefile and prepare feature_na labels."""
    if not poi_path:
        return gpd.GeoDataFrame()
    poi_path = Path(poi_path)
    if not poi_path.exists():
        warnings.warn(f'Points of interest shapefile not found: {poi_path}')
        return gpd.GeoDataFrame()
    try:
        pois = gpd.read_file(poi_path)
    except Exception as exc:
        warnings.warn(f'Could not read points of interest shapefile {poi_path}: {exc}')
        return gpd.GeoDataFrame()
    if pois.empty:
        warnings.warn(f'Points of interest shapefile is empty: {poi_path}')
        return gpd.GeoDataFrame()
    if pois.crs is None:
        warnings.warn(f'CRS undefined for points of interest shapefile {poi_path}; skipping')
        return gpd.GeoDataFrame()
    if 'feature_na' not in pois.columns:
        warnings.warn(f'Points of interest shapefile lacks required feature_na column: {poi_path}; skipping')
        return gpd.GeoDataFrame()
    try:
        pois = pois.to_crs(target_crs)
    except Exception as exc:
        warnings.warn(f'Could not reproject points of interest shapefile {poi_path}: {exc}')
        return gpd.GeoDataFrame()
    pois = pois[pois.geometry.notna() & ~pois.geometry.is_empty].copy()
    pois = pois[pois['feature_na'].notna() & (pois['feature_na'].astype(str).str.strip() != '')].copy()
    return pois


def draw_labels(
    ax,
    gdf,
    label_field='name',
    fontsize=LABEL_FONTSIZE,
    color='#1e1e1e',
    weight='normal',
    style='normal',
    xytext=(5, 4),
    halo_width=3.0,
    ha='left',
    va='bottom',
):
    if gdf is None or gdf.empty:
        return
    for _, row in gdf.iterrows():
        if label_field in row and row[label_field]:
            x, y = row.geometry.x, row.geometry.y
            txt = ax.annotate(
                str(row[label_field]),
                xy=(x, y),
                xycoords='data',
                xytext=xytext,
                textcoords='offset points',
                fontsize=fontsize,
                color=color,
                ha=ha,
                va=va,
                weight=weight,
                style=style,
                zorder=40,
                family=STYLE['font_family'],
            )
            txt.set_path_effects([
                PathEffects.withStroke(linewidth=halo_width, foreground='#fffdf6'),
                PathEffects.Normal(),
            ])


def draw_custom_north_arrow(ax, x=0.955, y=0.900, size=0.055):
    """Draw a bold custom north arrow in map axes coordinates."""
    pts = np.array([
        [x, y + size],
        [x - size * 0.35, y - size * 0.35],
        [x, y - size * 0.08],
        [x + size * 0.35, y - size * 0.35],
    ])
    ax.add_patch(Polygon(pts, closed=True, transform=ax.transAxes, facecolor='black', edgecolor='white', lw=0.5, zorder=50))
    ax.text(x, y - size * 0.58, 'N', transform=ax.transAxes, ha='center', va='center', fontsize=14, weight='bold', zorder=51)


def draw_custom_scale_bar(ax, length_m=1609.344, location=(0.03, 0.035)):
    """Draw a two-segment scale bar using map units."""
    xmin, xmax = ax.get_xlim()
    ymin, ymax = ax.get_ylim()
    x0 = xmin + (xmax - xmin) * location[0]
    y0 = ymin + (ymax - ymin) * location[1]
    bar_h = (ymax - ymin) * 0.008
    label_y = y0 + (ymax - ymin) * 0.022
    ax.add_patch(Rectangle((x0 - length_m * 0.08, y0 - bar_h * 2.2), length_m * 1.28, bar_h * 5.8,
                           facecolor='white', edgecolor='none', alpha=0.78, zorder=45))
    for i in range(2):
        ax.add_patch(Rectangle((x0 + i * length_m / 2, y0), length_m / 2, bar_h,
                               facecolor='black' if i == 0 else 'white', edgecolor='black', lw=0.8, zorder=50))
    for tick_x in [x0, x0 + length_m / 2, x0 + length_m]:
        ax.plot([tick_x, tick_x], [y0, y0 + bar_h * 1.8], color='black', lw=1.1, zorder=51)
    ax.text(x0, label_y, '0', ha='center', va='bottom', fontsize=9, weight='bold', zorder=51)
    ax.text(x0 + length_m / 2, label_y, '0.5', ha='center', va='bottom', fontsize=9, zorder=51)
    ax.text(x0 + length_m, label_y, '1 mi', ha='center', va='bottom', fontsize=9, zorder=51)


def draw_north_arrow(ax, x=0.955, y=0.900, size=0.055):
    draw_custom_north_arrow(ax, x=x, y=y, size=size)


def draw_scale_bar(ax, length_m=1609.344, location=(0.1, 0.03)):
    draw_custom_scale_bar(ax, length_m=length_m, location=location)


def draw_panel(ax, title=None):
    """Style a guidebook panel; adjust colors, border, and title size in STYLE."""
    ax.set_facecolor(STYLE['panel_face'])
    ax.set_xlim(0, 1)
    ax.set_ylim(0, 1)
    ax.set_xticks([])
    ax.set_yticks([])
    for spine in ax.spines.values():
        spine.set_visible(True)
        spine.set_color(STYLE['panel_edge'])
        spine.set_linewidth(STYLE['panel_lw'])
    if title:
        pad = STYLE['panel_pad']
        ax.text(
            pad, 0.93, title.upper(), transform=ax.transAxes,
            ha='left', va='top', fontsize=STYLE['section_size'],
            weight='bold', color=STYLE['text'], family=STYLE['font_family']
        )
        ax.plot(
            [pad, 1 - pad], [0.835, 0.835], transform=ax.transAxes,
            color=STYLE['panel_rule'], lw=STYLE['rule_lw']
        )


def add_panel_frame(ax, title=None):
    draw_panel(ax, title)


def parse_day_from_name(day_shp: Path):
    match = re.search(r'day\s*0*(\d+)', day_shp.stem, re.IGNORECASE)
    return int(match.group(1)) if match else None


def pretty_day_label(day_shp: Path):
    day_num = parse_day_from_name(day_shp)
    place = re.sub(r'^day\s*0*\d+[_\-\s]*', '', day_shp.stem, flags=re.IGNORECASE)
    place = place.replace('_', ' ').strip()
    if place:
        place = place.title() if place.isupper() or place.islower() else place
    if day_num and place:
        return f'Day {day_num} - {place}'
    if day_num:
        return f'Day {day_num}'
    return day_shp.stem.replace('_', ' ')


def read_day_yaml_metadata(day_num):
    """Lightweight parser for the local day.yaml files; avoids adding PyYAML."""
    if not day_num:
        return {}
    guide_dir = Path('DailyGuide')
    if not guide_dir.exists():
        return {}
    for yaml_path in guide_dir.glob('day*/day.yaml'):
        try:
            text = yaml_path.read_text(encoding='utf-8')
        except Exception:
            continue
        if not re.search(rf'(?m)^day:\s*{day_num}\s*$', text):
            continue
        meta = {}
        for key in ['title', 'subtitle']:
            m = re.search(rf'(?m)^{key}:\s*(.+?)\s*$', text)
            if m:
                meta[key] = m.group(1).strip().strip('"\'')
        route = re.search(r'(?ms)^route:[ \t]*\n(.*?)(?:\n\S|\Z)', text)
        if route:
            for key in ['start', 'end', 'camp', 'trailhead']:
                m = re.search(rf'(?m)^\s+{key}:\s*(.+?)\s*$', route.group(1))
                if m:
                    meta[key] = m.group(1).strip().strip('"\'')
        stats = re.search(r'(?ms)^trek_statistics:[ \t]*\n(.*?)(?:\n\S|\Z)', text)
        if stats:
            for key in ['mileage_mi', 'elevation_gain_ft', 'elevation_loss_ft',
                        'start_elevation_ft', 'end_elevation_ft', 'highest_point_ft',
                        'estimated_hiking_time', 'difficulty']:
                m = re.search(rf'(?m)^\s+{key}:\s*(.+?)\s*$', stats.group(1))
                if m:
                    meta[key] = m.group(1).strip().strip('"\'')
        return meta
    return {}


def parse_daily_hiking_stats(stats_path: Path):
    """Parse DailyGuide/dailiy-hiking-stats.md into metadata keyed by day number."""
    if not stats_path:
        return {}
    stats_path = Path(stats_path)
    if not stats_path.exists():
        warnings.warn(f'Daily hiking stats file not found: {stats_path}')
        return {}
    try:
        text = stats_path.read_text(encoding='utf-8-sig')
    except Exception as exc:
        warnings.warn(f'Could not read daily hiking stats file {stats_path}: {exc}')
        return {}

    day_stats = {}
    heading_pattern = re.compile(r'(?m)^##[ \t]+Day[ \t]+0*(\d+)[ \t]*(?:[-\u2013\u2014][ \t]*(.*?))?[ \t]*$')
    matches = list(heading_pattern.finditer(text))
    for idx, match in enumerate(matches):
        day_num = int(match.group(1))
        title = (match.group(2) or '').strip()
        body_start = match.end()
        body_end = matches[idx + 1].start() if idx + 1 < len(matches) else len(text)
        body = text[body_start:body_end]
        meta = {'title': title} if title else {}
        current_key = None
        for raw_line in body.splitlines():
            line = raw_line.strip()
            bullet = re.match(r'^-\s*([A-Za-z0-9_]+):\s*(.*)$', line)
            if bullet:
                current_key = bullet.group(1).strip()
                meta[current_key] = bullet.group(2).strip()
            elif current_key and line and not line.startswith('#'):
                meta[current_key] = f'{meta[current_key]} {line}'.strip()
        day_stats[day_num] = meta
    return day_stats


def clean_markdown_text(text: str) -> str:
    text = re.sub(r'(?m)^\s*[-*]\s*', '', text or '')
    text = re.sub(r'`([^`]+)`', r'\1', text)
    text = re.sub(r'\[([^\]]+)\]\([^)]+\)', r'\1', text)
    text = text.replace('â€”', '-').replace('â†’', '->').replace('â€™', "'")
    text = text.replace('â€œ', '"').replace('â€', '"')
    text = re.sub(r'\s+', ' ', text)
    return text.strip()


def parse_daily_markdown_sections(path: Path):
    """Parse day-heading markdown sections into compact text keyed by day number."""
    if not path:
        return {}
    path = Path(path)
    if not path.exists():
        warnings.warn(f'Daily markdown source not found: {path}')
        return {}
    try:
        text = path.read_text(encoding='utf-8-sig')
    except Exception as exc:
        warnings.warn(f'Could not read daily markdown source {path}: {exc}')
        return {}
    heading_pattern = re.compile(r'(?m)^##[ \t]+Day[ \t]+0*(\d+)\b.*$')
    matches = list(heading_pattern.finditer(text))
    sections = {}
    for idx, match in enumerate(matches):
        day_num = int(match.group(1))
        body_start = match.end()
        body_end = matches[idx + 1].start() if idx + 1 < len(matches) else len(text)
        body = text[body_start:body_end].strip()
        sections[day_num] = clean_markdown_text(body)
    return sections


def parse_daily_markdown_headings(path: Path):
    """Parse day-heading titles keyed by day number."""
    if not path:
        return {}
    path = Path(path)
    if not path.exists():
        warnings.warn(f'Daily markdown source not found: {path}')
        return {}
    try:
        text = path.read_text(encoding='utf-8-sig')
    except Exception as exc:
        warnings.warn(f'Could not read daily markdown source {path}: {exc}')
        return {}
    heading_pattern = re.compile(r'(?m)^##[ \t]+Day[ \t]+0*(\d+)[ \t]*(?:[-\u2013\u2014][ \t]*(.*?))?[ \t]*$')
    headings = {}
    for match in heading_pattern.finditer(text):
        day_num = int(match.group(1))
        heading = clean_markdown_text(match.group(2) or '')
        if heading:
            headings[day_num] = heading
    return headings


def parse_daily_scout_skills(path: Path):
    """Parse DailyGuide/daily-scout-skill.md into skill text keyed by day number."""
    if not path:
        return {}
    path = Path(path)
    if not path.exists():
        warnings.warn(f'Daily scout skill source not found: {path}')
        return {}
    try:
        lines = path.read_text(encoding='utf-8-sig').splitlines()
    except Exception as exc:
        warnings.warn(f'Could not read daily scout skill source {path}: {exc}')
        return {}

    skills = {}
    sequential_day = 1
    for line in lines:
        if not line.strip().startswith('|'):
            continue
        cells = [clean_markdown_text(cell.strip()) for cell in line.strip().strip('|').split('|')]
        if len(cells) < 2:
            continue
        first = cells[0].strip()
        second = cells[1].strip()
        if not first or first.lower() in {'day', '---'} or set(first) <= {'-'}:
            continue
        if not second or second.lower() in {'skill', '---'} or set(second) <= {'-'}:
            continue
        day_match = re.search(r'\d+', first)
        if day_match:
            day_num = int(day_match.group(0))
        else:
            day_num = sequential_day
            sequential_day += 1
        skills[day_num] = f'{first}: {second}'
    return skills


def parse_clone_wars_quotes(path: Path):
    """Parse the Daily Clone Wars Quotes table into quote text keyed by day number."""
    if not path:
        return {}
    path = Path(path)
    if not path.exists():
        warnings.warn(f'Clone Wars quote source not found: {path}')
        return {}
    try:
        lines = path.read_text(encoding='utf-8-sig').splitlines()
    except Exception as exc:
        warnings.warn(f'Could not read Clone Wars quote source {path}: {exc}')
        return {}
    quotes = {}
    for line in lines:
        if not line.strip().startswith('|'):
            continue
        cells = [cell.strip() for cell in line.strip().strip('|').split('|')]
        if len(cells) < 3 or cells[0].lower() in {'day', '---'}:
            continue
        match = re.search(r'Day\s+0*(\d+)', cells[0], re.IGNORECASE)
        if not match:
            continue
        quote = clean_markdown_text(cells[2])
        if quote:
            quotes[int(match.group(1))] = quote
    return quotes


def display_value(value, suffix=''):
    if value is None or value == '' or str(value).upper() in {'TBD', 'N/A', 'NONE'}:
        return 'N/A'
    if suffix and isinstance(value, str):
        stripped = value.strip()
        if re.search(r"['\"]|ft\b|mi\b|miles?\b", stripped, re.IGNORECASE):
            return stripped
    return f'{value}{suffix}'


def merge_line_geometry(gdf):
    geom = unary_union([geom for geom in gdf.geometry if geom is not None and not geom.is_empty])
    try:
        geom = linemerge(geom)
    except Exception:
        pass
    if geom.geom_type == 'MultiLineString':
        return max(list(geom.geoms), key=lambda g: g.length)
    return geom


def sample_elevation_profile(trail_gdf, dem_img, dem_transform, samples=180):
    if dem_img is None or dem_transform is None:
        return None
    line = merge_line_geometry(trail_gdf)
    if line is None or line.is_empty or line.length <= 0:
        return None
    band = dem_img[0].astype('float64') if dem_img.ndim == 3 else dem_img.astype('float64')
    nodata_mask = ~np.isfinite(band)
    distances = np.linspace(0, line.length, samples)
    elevations = []
    inv = ~dem_transform
    for dist in distances:
        point = line.interpolate(dist)
        col, row = inv * (point.x, point.y)
        row = int(round(row))
        col = int(round(col))
        if row < 0 or col < 0 or row >= band.shape[0] or col >= band.shape[1] or nodata_mask[row, col]:
            elevations.append(np.nan)
        else:
            elevations.append(band[row, col])
    elevations = np.array(elevations, dtype='float64')
    valid = np.isfinite(elevations)
    if valid.sum() < 3:
        return None
    median = np.nanmedian(elevations)
    if median < 5000:
        elevations *= 3.28084
    miles = distances / 1609.344
    return miles, elevations


def make_out_and_back_peak_profile(profile):
    if profile is None:
        return None
    miles, elevations = profile
    miles = np.asarray(miles, dtype='float64')
    elevations = np.asarray(elevations, dtype='float64')
    if len(miles) < 3 or len(elevations) < 3:
        return profile

    total_miles = float(np.nanmax(miles) - np.nanmin(miles))
    if not np.isfinite(total_miles) or total_miles <= 0:
        return profile

    outbound_elevations = elevations[::-1]
    source_miles = np.linspace(0, total_miles / 2, len(outbound_elevations))
    valid = np.isfinite(outbound_elevations)
    if valid.sum() < 3:
        return profile
    outbound_elevations = np.interp(source_miles, source_miles[valid], outbound_elevations[valid])

    half_count = max(3, int(np.ceil(len(outbound_elevations) / 2)))
    outbound_miles = np.linspace(0, total_miles / 2, half_count)
    outbound = np.interp(outbound_miles, source_miles, outbound_elevations)

    return_miles = np.linspace(total_miles / 2, total_miles, half_count)[1:]
    return_profile = outbound[::-1][1:]
    out_and_back_miles = np.concatenate([outbound_miles, return_miles])
    out_and_back_elevations = np.concatenate([outbound, return_profile])
    return out_and_back_miles, out_and_back_elevations


def parse_elevation_feet(value):
    if value is None:
        return None
    text = str(value).strip()
    if text.upper() in {'TBD', 'N/A', 'NONE', ''}:
        return None
    match = re.search(r'-?[\d,]+(?:\.\d+)?', text)
    if not match:
        return None
    return float(match.group(0).replace(',', ''))


def reverse_profile(profile):
    if profile is None:
        return None
    miles, elevations = profile
    miles = np.asarray(miles, dtype='float64')
    elevations = np.asarray(elevations, dtype='float64')
    return miles.copy(), elevations[::-1].copy()


def anchor_profile_endpoint(profile, target_end_ft):
    if profile is None or target_end_ft is None:
        return profile
    miles, elevations = profile
    miles = np.asarray(miles, dtype='float64')
    elevations = np.asarray(elevations, dtype='float64')
    valid_idx = np.where(np.isfinite(elevations))[0]
    if len(valid_idx) < 3:
        return profile

    end_idx = valid_idx[-1]
    end_delta = target_end_ft - elevations[end_idx]
    if abs(end_delta) < 1:
        return miles.copy(), elevations.copy()

    ramp = np.zeros(len(elevations), dtype='float64')
    if end_idx > 0:
        ramp[:end_idx + 1] = np.linspace(0, end_delta, end_idx + 1)
        ramp[end_idx + 1:] = end_delta
    else:
        ramp[:] = end_delta
    adjusted = elevations.copy()
    adjusted[np.isfinite(adjusted)] += ramp[np.isfinite(adjusted)]
    return miles.copy(), adjusted


def make_day11_hill_profile(profile, target_start_ft, target_end_ft):
    if profile is None or target_start_ft is None or target_end_ft is None:
        return profile
    miles, elevations = profile
    miles = np.asarray(miles, dtype='float64')
    elevations = np.asarray(elevations, dtype='float64')
    valid_idx = np.where(np.isfinite(elevations))[0]
    if len(valid_idx) < 3:
        return profile

    high_ft = float(np.nanmax(elevations[valid_idx]))
    peak_fraction = 0.42
    mile_start = miles[valid_idx[0]]
    mile_end = miles[valid_idx[-1]]
    peak_mile = mile_start + (mile_end - mile_start) * peak_fraction

    adjusted = elevations.copy()
    valid_miles = miles[valid_idx]
    base = np.interp(
        valid_miles,
        [mile_start, peak_mile, mile_end],
        [target_start_ft, high_ft, target_end_ft],
    )

    sampled_trend = np.interp(
        valid_miles,
        [valid_miles[0], valid_miles[-1]],
        [elevations[valid_idx[0]], elevations[valid_idx[-1]]],
    )
    residual = elevations[valid_idx] - sampled_trend
    residual -= np.interp(valid_miles, [valid_miles[0], valid_miles[-1]], [residual[0], residual[-1]])
    residual = np.clip(residual * 0.25, -45, 45)

    adjusted[valid_idx] = base + residual
    adjusted[valid_idx[0]] = target_start_ft
    adjusted[valid_idx[-1]] = target_end_ft
    return miles.copy(), adjusted


def anchor_profile_endpoints(profile, target_start_ft, target_end_ft):
    if profile is None or target_start_ft is None or target_end_ft is None:
        return profile
    miles, elevations = profile
    miles = np.asarray(miles, dtype='float64')
    elevations = np.asarray(elevations, dtype='float64')
    valid_idx = np.where(np.isfinite(elevations))[0]
    if len(valid_idx) < 3:
        return profile

    start_idx = valid_idx[0]
    end_idx = valid_idx[-1]
    if end_idx <= start_idx:
        return profile

    start_delta = target_start_ft - elevations[start_idx]
    end_delta = target_end_ft - elevations[end_idx]
    ramp = np.zeros(len(elevations), dtype='float64')
    ramp[:start_idx + 1] = start_delta
    ramp[start_idx:end_idx + 1] = np.linspace(start_delta, end_delta, end_idx - start_idx + 1)
    ramp[end_idx:] = end_delta

    adjusted = elevations.copy()
    adjusted[np.isfinite(adjusted)] += ramp[np.isfinite(adjusted)]
    return miles.copy(), adjusted


def adjust_daily_profile(day_num, profile, meta):
    if profile is None:
        return None
    if day_num in {3, 5, 8, 9, 10}:
        profile = reverse_profile(profile)
        target_start = parse_elevation_feet(meta.get('start_elevation') or meta.get('start_elevation_ft'))
        target_end = parse_elevation_feet(meta.get('end_elevation') or meta.get('end_elevation_ft'))
        profile = anchor_profile_endpoints(profile, target_start, target_end)
    if day_num == 6:
        target_end = parse_elevation_feet(meta.get('end_elevation') or meta.get('end_elevation_ft'))
        profile = anchor_profile_endpoint(profile, target_end)
    if day_num == 7:
        profile = make_out_and_back_peak_profile(profile)
    if day_num == 11:
        target_start = parse_elevation_feet(meta.get('start_elevation') or meta.get('start_elevation_ft'))
        target_end = parse_elevation_feet(meta.get('end_elevation') or meta.get('end_elevation_ft'))
        profile = make_day11_hill_profile(profile, target_start, target_end)
    return profile


def sample_full_trip_profile(trails_gdf, dem_img, dem_transform, samples_per_day=80):
    if dem_img is None or dem_transform is None or trails_gdf is None or trails_gdf.empty:
        return None
    cumulative_miles = []
    cumulative_elevations = []
    day_boundaries = []
    offset = 0.0
    for day_num, day_group in trails_gdf.groupby('day_num', sort=True):
        try:
            line = merge_line_geometry(day_group)
        except Exception:
            continue
        if line is None or line.is_empty or line.length <= 0:
            continue
        samples = max(24, int(samples_per_day))
        profile = sample_elevation_profile(day_group, dem_img, dem_transform, samples=samples)
        if profile is None:
            offset += line.length / 1609.344
            day_boundaries.append((offset, day_num))
            continue
        miles, elevations = profile
        miles = miles + offset
        if cumulative_miles:
            miles = miles[1:]
            elevations = elevations[1:]
        cumulative_miles.extend(miles.tolist())
        cumulative_elevations.extend(elevations.tolist())
        offset += line.length / 1609.344
        day_boundaries.append((offset, day_num))
    if len(cumulative_miles) < 3:
        return None
    return np.array(cumulative_miles), np.array(cumulative_elevations), day_boundaries


def profile_stats(profile):
    if profile is None:
        return {}
    _, elevations = profile
    valid = elevations[np.isfinite(elevations)]
    if len(valid) < 3:
        return {}
    diffs = np.diff(elevations)
    diffs = diffs[np.isfinite(diffs)]
    return {
        'gain_ft': int(round(np.sum(diffs[diffs > 0]))) if len(diffs) else None,
        'loss_ft': int(round(abs(np.sum(diffs[diffs < 0])))) if len(diffs) else None,
        'high_ft': int(round(np.nanmax(elevations))),
        'low_ft': int(round(np.nanmin(elevations))),
    }


def difficulty_color(value):
    text = str(value or '').lower()
    if any(word in text for word in ['hard', 'very hard']):
        return '#b3261e'
    if any(word in text for word in ['moderate', 'medium']):
        return '#b7791f'
    if any(word in text for word in ['easy', 'base camp', 'travel day']):
        return '#2f7d32'
    return STYLE['text']


def extract_water_camping_note(program):
    text = clean_markdown_text(program)
    if not text or text == 'N/A':
        return ''
    patterns = [
        r'Dry Camp(?:\s*\([^)]+\))?',
        r'Water\s*(?:@|at)\s*[^;,.]+',
        r'Trail Camp',
        r'Commissary',
        r'Trading Post',
        r'Chuckwagon Dinner',
    ]
    notes = []
    for pattern in patterns:
        for match in re.finditer(pattern, text, flags=re.IGNORECASE):
            note = match.group(0).strip()
            if note and note.lower() not in {n.lower() for n in notes}:
                notes.append(note)
    return ' | '.join(notes[:2])


def route_subtitle(day_num, day_label, meta, daily_content):
    start = display_value(meta.get('start') or meta.get('trailhead'))
    end = display_value(meta.get('end') or meta.get('camp') or meta.get('title'))
    if start != 'N/A' and end != 'N/A':
        return f'{start} to {end}'
    headings = daily_content.get('look_for_this_titles', {}) if daily_content else {}
    heading = headings.get(day_num)
    if heading:
        return heading
    place = re.sub(r'^Day\s+0*\d+\s*[-\u2013\u2014]\s*', '', day_label).strip()
    return place or 'Philmont Scout Ranch | Cimarron, New Mexico'


def draw_header(ax, title, subtitle='Philmont Scout Ranch | Cimarron, New Mexico', note='', quote='', scout_skill=''):
    ax.set_facecolor(HEADER_FACE)
    ax.set_xticks([])
    ax.set_yticks([])
    for spine in ax.spines.values():
        spine.set_visible(False)
    ax.text(0.018, 0.66, title, color='white', fontsize=STYLE['title_size'], weight='bold',
            ha='left', va='center', transform=ax.transAxes, family=STYLE['font_family'])
    ax.text(0.018, 0.34, subtitle, color='white', fontsize=STYLE['subtitle_size'],
            ha='left', va='center', transform=ax.transAxes, family=STYLE['font_family'])
    if quote:
        quote_text = textwrap.shorten(clean_markdown_text(quote), width=82, placeholder='...')
        ax.text(0.018, 0.135, quote_text, color=STYLE['header_rule'],
                fontsize=STYLE['small_size'] + 0.2, ha='left', va='center',
                transform=ax.transAxes, family=STYLE['font_family'],
                style='italic')
    if scout_skill:
        skill_text = textwrap.shorten(f'Scout Skill: {clean_markdown_text(scout_skill)}', width=58, placeholder='...')
        ax.text(
            0.405, 0.415, skill_text, color=STYLE['header_rule'],
            fontsize=STYLE['small_size'] + 0.35, ha='left', va='center',
            transform=ax.transAxes, family=STYLE['font_family'], weight='bold'
        )
    ax.text(0.727, 0.63, 'Philmont Scout Ranch', color='white', fontsize=STYLE['subtitle_size'] + 1.1,
            weight='bold', ha='left', va='center', transform=ax.transAxes)
    ax.text(0.727, 0.38, 'Troop 211/628-G-02', color='white', fontsize=STYLE['subtitle_size'] + 0.3,
            ha='left', va='center', transform=ax.transAxes)
    if note:
        ax.text(0.727, 0.15, note, color=STYLE['header_rule'], fontsize=STYLE['small_size'] + 0.4,
                ha='left', va='center', transform=ax.transAxes, weight='bold')
    ax.plot([0, 1], [0.02, 0.02], transform=ax.transAxes, color=STYLE['header_rule'], lw=0.7)
    shield = Polygon([[0.947, 0.10], [0.988, 0.10], [0.995, 0.72], [0.967, 0.95], [0.939, 0.72]],
                     transform=ax.transAxes, closed=True, facecolor='#5f6f3a', edgecolor='#c77a32', lw=2)
    ax.add_patch(shield)
    ax.text(0.967, 0.62, 'PHILMONT', color='white', fontsize=7.5, weight='bold',
            rotation=18, ha='center', va='center', transform=ax.transAxes)
    ax.text(0.967, 0.22, 'RANCH', color='white', fontsize=7.5, weight='bold',
            ha='center', va='center', transform=ax.transAxes)


def draw_custom_legend(ax):
    """Draw a precise single-column legend; adjust spacing via STYLE values."""
    draw_panel(ax, 'Map Legend')
    items = [
        ('line', 'Daily Trail', TRAIL_COLOR, '-'),
        ('marker', 'Camps', CAMP_COLOR, CAMP_MARKER),
        ('marker', 'Point of Interest', POI_COLOR, POI_MARKER),
        ('line', 'Reference Roads', ROAD_COLOR, '-'),
        ('line', 'Reference Trails', REFERENCE_TRAIL_COLOR, (0, (4, 3))),
        ('patch', 'Geology (See Key Below)', STYLE['geology_overlay'], None),
        ('line', 'Water', WATER_COLOR, '--'),
    ]
    sx = 0.150
    tx = 0.270
    y = 0.720
    y_step = 0.092
    for kind, label, color, symbol in items:
        if kind == 'line':
            ax.plot([sx - 0.035, sx + 0.035], [y, y], transform=ax.transAxes,
                    color=color, lw=STYLE['legend_line_width'], linestyle=symbol,
                    solid_capstyle='round')
        elif kind == 'patch':
            ax.add_patch(Rectangle((sx - 0.032, y - 0.027), 0.064, 0.054,
                                   transform=ax.transAxes, facecolor=color,
                                   edgecolor=STYLE['panel_rule'], lw=0.35,
                                   alpha=GEOLOGY_ALPHA))
        else:
            ax.plot([sx], [y], marker=symbol, color=color, markerfacecolor=color,
                    markeredgecolor='white', markeredgewidth=0.5,
                    markersize=STYLE['legend_marker_size'], transform=ax.transAxes,
                    linestyle='None')
        ax.text(tx, y, label, transform=ax.transAxes, ha='left', va='center',
                fontsize=STYLE['body_size'], color=STYLE['text'],
                family=STYLE['font_family'])
        y -= y_step


def draw_sidebar_legend(ax):
    draw_custom_legend(ax)


def draw_geology_key(ax):
    draw_panel(ax, 'Geology Key')
    units = [
        ('Qc', 'Quaternary Alluvium', '#e8d9a2'),
        ('Ktr', 'Truchas Formation', '#cfba84'),
        ('Kw', 'Wall Mountain Tuff', '#c98973'),
        ('Kt', 'Tertiary Volcanics', '#e2c9a8'),
        ('Kpn', 'Pierre Shale', '#aeba92'),
        ('Tpc', 'Permian Sediments', '#c99970'),
    ]
    y = 0.735
    for code, label, color in units:
        ax.add_patch(Rectangle((0.075, y - 0.035), 0.145, 0.070, transform=ax.transAxes,
                               facecolor=color, edgecolor=STYLE['panel_rule'], lw=0.35))
        ax.text(0.148, y, code, transform=ax.transAxes, ha='center', va='center',
                fontsize=STYLE['small_size'], weight='bold', color=STYLE['text'])
        ax.text(0.255, y, label, transform=ax.transAxes, ha='left', va='center',
                fontsize=STYLE['small_size'], color=STYLE['text'])
        y -= 0.103


def wrap_text_to_panel(content, wrap_width, max_lines):
    """Return wrapped text, preferring fuller panels before shortening."""
    if max_lines <= 0:
        return ''
    wrapped = textwrap.wrap(
        content,
        width=max(12, wrap_width),
        break_long_words=False,
        break_on_hyphens=False,
    )
    if len(wrapped) <= max_lines:
        return '\n'.join(wrapped)
    shortened = textwrap.shorten(content, width=max(20, wrap_width * max_lines), placeholder='...')
    return '\n'.join(textwrap.wrap(
        shortened,
        width=max(12, wrap_width),
        break_long_words=False,
        break_on_hyphens=False,
    ))


def text_width_px(ax, text, font_size, weight='normal'):
    cache = getattr(ax, '_text_width_cache', {})
    key = (text, round(float(font_size), 2), weight)
    if key in cache:
        return cache[key]
    probe = ax.text(
        0, 0, text, transform=ax.transAxes, fontsize=font_size,
        weight=weight, family=STYLE['font_family'], alpha=0
    )
    try:
        renderer = ax.figure.canvas.get_renderer()
    except Exception:
        ax.figure.canvas.draw()
        renderer = ax.figure.canvas.get_renderer()
    width = probe.get_window_extent(renderer=renderer).width
    probe.remove()
    cache[key] = width
    ax._text_width_cache = cache
    return width


def wrap_text_by_pixel_width(ax, content, max_width_px, font_size):
    words = content.split()
    lines = []
    current = ''
    for word in words:
        trial = f'{current} {word}'.strip()
        if not current or text_width_px(ax, trial, font_size) <= max_width_px:
            current = trial
            continue
        lines.append(current)
        current = word
        while text_width_px(ax, current, font_size) > max_width_px and len(current) > 8:
            split_at = max(8, int(len(current) * max_width_px / text_width_px(ax, current, font_size)))
            lines.append(current[:split_at - 1] + '-')
            current = current[split_at - 1:]
    if current:
        lines.append(current)
    return lines


def fit_text_to_panel(ax, content, left, right, top, bottom):
    fig = ax.figure
    bbox = ax.get_position()
    panel_width_px = bbox.width * fig.get_figwidth() * fig.dpi
    panel_height_px = bbox.height * fig.get_figheight() * fig.dpi
    max_width_px = panel_width_px * max(0.10, right - left)
    max_height_px = panel_height_px * max(0.10, top - bottom)
    min_font = 4.45
    for font_size in np.arange(STYLE['small_size'] + 0.20, min_font - 0.01, -0.15):
        linespacing = 0.88 if font_size <= 5.15 else 0.92
        lines = wrap_text_by_pixel_width(ax, content, max_width_px, font_size)
        line_height_px = font_size * fig.dpi / 72 * linespacing
        max_lines = max(1, int(max_height_px / line_height_px))
        if len(lines) <= max_lines:
            return '\n'.join(lines), font_size, linespacing

    font_size = min_font
    linespacing = 0.86
    line_height_px = font_size * fig.dpi / 72 * linespacing
    max_lines = max(1, int(max_height_px / line_height_px))
    words = content.split()
    shortened = content
    while words:
        candidate = ' '.join(words) + '...'
        lines = wrap_text_by_pixel_width(ax, candidate, max_width_px, font_size)
        if len(lines) <= max_lines:
            shortened = candidate
            break
        words.pop()
    return '\n'.join(wrap_text_by_pixel_width(ax, shortened, max_width_px, font_size)[:max_lines]), font_size, linespacing


def draw_panel_icon(ax, icon_key, x, y, size=0.042):
    color = STYLE['muted_text']
    lw = 0.85
    if icon_key == 'geology':
        ax.add_patch(Polygon(
            [(x - size * 0.75, y - size * 0.42), (x - size * 0.18, y + size * 0.48),
             (x + size * 0.08, y - size * 0.03), (x + size * 0.28, y + size * 0.32),
             (x + size * 0.78, y - size * 0.42)],
            closed=False, transform=ax.transAxes, fill=False, edgecolor=color,
            lw=lw, joinstyle='round', capstyle='round'
        ))
        for offset in (-0.17, -0.36):
            ax.plot(
                [x - size * 0.62, x + size * 0.64], [y + size * offset, y + size * offset],
                transform=ax.transAxes, color=color, lw=0.55, solid_capstyle='round'
            )
    elif icon_key == 'riddle':
        ax.text(
            x, y - size * 0.12, '?', transform=ax.transAxes, ha='center', va='center',
            fontsize=STYLE['section_size'] + 1.2, weight='bold',
            color=color, family=STYLE['font_family']
        )
    elif icon_key == 'look':
        ax.add_patch(Ellipse(
            (x - size * 0.33, y), size * 0.60, size * 0.62, transform=ax.transAxes,
            fill=False, edgecolor=color, lw=lw
        ))
        ax.add_patch(Ellipse(
            (x + size * 0.33, y), size * 0.60, size * 0.62, transform=ax.transAxes,
            fill=False, edgecolor=color, lw=lw
        ))
        ax.plot(
            [x - size * 0.03, x + size * 0.03], [y + size * 0.10, y + size * 0.10],
            transform=ax.transAxes, color=color, lw=lw, solid_capstyle='round'
        )
        ax.plot(
            [x - size * 0.62, x - size * 0.90], [y - size * 0.25, y - size * 0.55],
            transform=ax.transAxes, color=color, lw=lw, solid_capstyle='round'
        )
        ax.plot(
            [x + size * 0.62, x + size * 0.90], [y - size * 0.25, y - size * 0.55],
            transform=ax.transAxes, color=color, lw=lw, solid_capstyle='round'
        )
    elif icon_key == 'astronomy':
        ax.add_patch(Ellipse(
            (x - size * 0.15, y), size * 0.74, size * 0.88, transform=ax.transAxes,
            fill=False, edgecolor=color, lw=lw
        ))
        ax.add_patch(Ellipse(
            (x + size * 0.03, y + size * 0.08), size * 0.62, size * 0.84,
            transform=ax.transAxes, facecolor=STYLE['panel_face'], edgecolor='none'
        ))
        ax.add_patch(Polygon(
            [(x + size * 0.58, y + size * 0.49), (x + size * 0.64, y + size * 0.33),
             (x + size * 0.80, y + size * 0.27), (x + size * 0.64, y + size * 0.21),
             (x + size * 0.58, y + size * 0.05), (x + size * 0.52, y + size * 0.21),
             (x + size * 0.36, y + size * 0.27), (x + size * 0.52, y + size * 0.33)],
            closed=True, transform=ax.transAxes, facecolor=color, edgecolor=color, lw=0
        ))


def draw_daily_text_panel(ax, title, text, max_lines=None, wrap_width=None, fill_panel=False, icon_key=None):
    draw_panel(ax)
    pad = 0.065
    right_pad = 0.060
    body_top = 0.705
    body_bottom = 0.090
    title_size = STYLE['section_size']
    if len(title) > 17:
        title_size -= 1.45
    elif len(title) > 12:
        title_size -= 0.85
    title_x = pad
    if icon_key:
        draw_panel_icon(ax, icon_key, pad + 0.030, 0.882, size=0.037)
        title_x = pad + 0.087
    ax.text(
        title_x, 0.930, title.upper(), transform=ax.transAxes,
        ha='left', va='top', fontsize=title_size,
        weight='bold', color=STYLE['text'], family=STYLE['font_family']
    )
    ax.plot(
        [pad, 1 - pad], [0.805, 0.805], transform=ax.transAxes,
        color=STYLE['panel_rule'], lw=STYLE['rule_lw']
    )
    content = clean_markdown_text(text) if text else 'N/A'
    if not content:
        content = 'N/A'
    if fill_panel:
        rendered_text, body_size, linespacing = fit_text_to_panel(
            ax, content, pad, 1 - right_pad, body_top, body_bottom
        )
    else:
        wrapped = textwrap.wrap(content, width=wrap_width or 52, max_lines=max_lines or 11, placeholder='...')
        rendered_text = '\n'.join(wrapped)
        body_size = STYLE['small_size'] - 1.25
        linespacing = 0.94
    ax.text(
        pad, body_top, rendered_text, transform=ax.transAxes,
        ha='left', va='top', fontsize=body_size,
        color=STYLE['text'], family=STYLE['font_family'], linespacing=linespacing,
        clip_on=False
    )


def draw_trail_summary(ax, day_label, trail_gdf, meta, profile):
    day_num = parse_day_from_name(Path(day_label))
    summary_title = f'Day {day_num} - Trail Summary' if day_num else 'Trail Summary'
    draw_panel(ax, summary_title)
    pstats = profile_stats(profile)
    distance = meta.get('mileage') or meta.get('mileage_mi')
    if not distance:
        distance = f'{trail_gdf.length.sum() / 1609.344:.1f}'
    rows = [
        ('Distance', f'{distance} mi'),
        ('Gain', display_value(meta.get('elevation_gain') or meta.get('elevation_gain_ft') or pstats.get('gain_ft'), ' ft')),
        ('Loss', display_value(meta.get('elevation_loss') or meta.get('elevation_loss_ft') or pstats.get('loss_ft'), ' ft')),
        ('Start Elev.', display_value(meta.get('start_elevation') or meta.get('start_elevation_ft'), ' ft')),
        ('End Elev.', display_value(meta.get('end_elevation') or meta.get('end_elevation_ft'), ' ft')),
        ('High Point', display_value(meta.get('highest_point') or meta.get('highest_point_ft') or pstats.get('high_ft'), ' ft')),
        ('Difficulty', display_value(meta.get('difficulty'))),
        ('Hiking Time', display_value(meta.get('estimated_hiking_time'))),
    ]
    y = 0.750
    row_step = 0.061
    for idx, (label, value) in enumerate(rows):
        if idx % 2 == 0:
            ax.add_patch(Rectangle((0.055, y - 0.026), 0.890, 0.047, transform=ax.transAxes,
                                   facecolor='#eee3d0', edgecolor='none', alpha=0.55))
        value = str(value).replace('-', ' ')
        value_size = STYLE['small_size'] - 0.8 if len(value) > 16 else STYLE['small_size'] - 0.25
        ax.text(0.075, y, label, transform=ax.transAxes, ha='left', va='center',
                fontsize=STYLE['small_size'] - 0.25, color=STYLE['text'], family=STYLE['font_family'])
        ax.plot([0.360, 0.665], [y - 0.008, y - 0.008], transform=ax.transAxes,
                color=STYLE['panel_rule'], lw=0.40, linestyle=(0, (1, 2)), alpha=0.95)
        value_color = difficulty_color(value) if label == 'Difficulty' else STYLE['text']
        value_weight = 'bold' if label == 'Difficulty' and value_color != STYLE['text'] else 'normal'
        ax.text(0.930, y, value, transform=ax.transAxes, ha='right', va='center',
                fontsize=value_size, color=value_color, weight=value_weight, family=STYLE['font_family'])
        y -= row_step
    program = display_value(meta.get('program'))
    if program != 'N/A':
        wrapped = textwrap.wrap(program, width=38, max_lines=2, placeholder='...')
        ax.plot([0.060, 0.940], [0.190, 0.190], transform=ax.transAxes,
                color=STYLE['panel_rule'], lw=STYLE['rule_lw'])
        ax.text(0.075, 0.145, 'Program', transform=ax.transAxes, ha='left', va='center',
                fontsize=STYLE['small_size'] - 0.55, weight='bold',
                color=STYLE['muted_text'], family=STYLE['font_family'])
        ax.text(0.075, 0.080, '\n'.join(wrapped), transform=ax.transAxes, ha='left', va='center',
                fontsize=STYLE['small_size'] - 0.95, color=STYLE['text'],
                family=STYLE['font_family'], linespacing=1.08)


def draw_elevation_profile(ax, profile):
    draw_panel(ax, 'Elevation Profile')
    if profile is None:
        ax.text(0.5, 0.47, 'Elevation data unavailable', transform=ax.transAxes,
                ha='center', va='center', fontsize=STYLE['body_size'], color=STYLE['muted_text'])
        return
    miles, elevations = profile
    ax.set_xticks([])
    ax.set_yticks([])
    inner = ax.inset_axes([0.155, 0.175, 0.790, 0.635])
    inner.set_facecolor('#f7f2e8')
    ymin = np.nanmin(elevations)
    ymax = np.nanmax(elevations)
    ypad = max((ymax - ymin) * 0.16, 120)
    inner.plot(miles, elevations, color='black', lw=1.45, solid_capstyle='round')
    inner.fill_between(miles, elevations, ymin - ypad, color='#b99f76', alpha=0.14)
    valid_idx = np.where(np.isfinite(elevations))[0]
    if len(valid_idx):
        start_idx = valid_idx[0]
        end_idx = valid_idx[-1]
        high_idx = valid_idx[np.nanargmax(elevations[valid_idx])]
        start_offset = (4, 7)
        start_ha = 'left'
        end_offset = (-4, 7)
        end_ha = 'right'
        high_offset = (0, 9)
        high_ha = 'center'
        if high_idx == end_idx:
            end_offset = (-8, 4)
            high_offset = (-3, 16)
            high_ha = 'right'
        elif high_idx == start_idx:
            start_offset = (4, 3)
            high_offset = (6, 12)
            high_ha = 'left'
        marker_specs = [
            ('Start', start_idx, '#2f7d32', start_offset, start_ha),
            ('End', end_idx, '#5f4b8b', end_offset, end_ha),
            ('High', high_idx, '#b3261e', high_offset, high_ha),
        ]
        used = set()
        for label, idx, color, offset, ha in marker_specs:
            key = (round(float(miles[idx]), 2), round(float(elevations[idx]), 0), label)
            if key in used:
                continue
            used.add(key)
            inner.plot(miles[idx], elevations[idx], marker='o', markersize=3.8,
                       color=color, markeredgecolor='white', markeredgewidth=0.45, zorder=5)
            inner.annotate(
                label, xy=(miles[idx], elevations[idx]), xytext=offset,
                textcoords='offset points', ha=ha, va='bottom',
                fontsize=STYLE['small_size'] - 1.0, color=color,
                weight='bold', zorder=6,
                path_effects=[PathEffects.withStroke(linewidth=1.7, foreground='#fffdf6'), PathEffects.Normal()],
            )
    inner.set_xlim(np.nanmin(miles), np.nanmax(miles))
    inner.set_ylim(ymin - ypad, ymax + ypad)
    inner.grid(color='#d2c8b6', lw=0.36, linestyle='-', alpha=0.78)
    inner.set_xlabel('Distance (mi)', fontsize=STYLE['small_size'] - 0.1, labelpad=1.0)
    inner.set_ylabel('Elevation (ft)', fontsize=STYLE['small_size'] - 0.1, labelpad=1.0)
    inner.tick_params(axis='both', labelsize=STYLE['small_size'] - 0.7, length=2, pad=1.5, colors=STYLE['muted_text'])
    inner.xaxis.set_major_locator(MaxNLocator(nbins=5))
    inner.yaxis.set_major_locator(MaxNLocator(nbins=4))
    inner.yaxis.set_major_formatter(FuncFormatter(lambda v, _: f'{int(v):,}'))
    for spine in inner.spines.values():
        spine.set_color(STYLE['panel_rule'])
        spine.set_linewidth(0.42)


def draw_full_trip_profile(ax, full_profile, profile_image=None):
    draw_panel(ax, 'Full Trip Elevation Profile')
    if profile_image and Path(profile_image).exists():
        try:
            img = plt.imread(str(profile_image))
            ax.imshow(img, extent=(0.030, 0.970, 0.105, 0.790),
                      transform=ax.transAxes, aspect='auto', zorder=2)
            ax.text(0.970, 0.045, 'Source: References/Philmont/Trek 12-15.pdf',
                    transform=ax.transAxes, ha='right', va='center',
                    fontsize=STYLE['small_size'] - 0.65,
                    color=STYLE['muted_text'], family=STYLE['font_family'])
            return
        except Exception as exc:
            warnings.warn(f'Could not read overview elevation profile image {profile_image}: {exc}')
    if full_profile is None:
        ax.text(0.5, 0.47, 'Elevation profile unavailable', transform=ax.transAxes,
                ha='center', va='center', fontsize=STYLE['body_size'], color=STYLE['muted_text'])
        return
    miles, elevations, day_boundaries = full_profile
    valid = np.isfinite(elevations)
    if valid.sum() < 3:
        ax.text(0.5, 0.47, 'Elevation profile unavailable', transform=ax.transAxes,
                ha='center', va='center', fontsize=STYLE['body_size'], color=STYLE['muted_text'])
        return
    inner = ax.inset_axes([0.060, 0.230, 0.910, 0.550])
    inner.set_facecolor('#f7f2e8')
    ymin = np.nanmin(elevations)
    ymax = np.nanmax(elevations)
    ypad = max((ymax - ymin) * 0.18, 180)
    inner.plot(miles, elevations, color='black', lw=1.10, solid_capstyle='round')
    inner.fill_between(miles, elevations, ymin - ypad, color='#b99f76', alpha=0.13)
    for boundary_mi, day_num in day_boundaries[:-1]:
        inner.axvline(boundary_mi, color='#d2c8b6', lw=0.35, alpha=0.75, zorder=0)
    high_idx = np.nanargmax(elevations)
    inner.plot(miles[high_idx], elevations[high_idx], marker='o', color='#b3261e',
               markeredgecolor='white', markeredgewidth=0.45, markersize=3.8, zorder=5)
    inner.annotate('High', xy=(miles[high_idx], elevations[high_idx]), xytext=(0, 7),
                   textcoords='offset points', ha='center', va='bottom',
                   fontsize=STYLE['small_size'] - 1.0, color='#b3261e',
                   weight='bold',
                   path_effects=[PathEffects.withStroke(linewidth=1.7, foreground='#fffdf6'), PathEffects.Normal()])
    inner.set_xlim(np.nanmin(miles), np.nanmax(miles))
    inner.set_ylim(ymin - ypad, ymax + ypad)
    inner.grid(color='#d2c8b6', lw=0.30, linestyle='-', alpha=0.70)
    inner.set_xlabel('Cumulative Distance (mi)', fontsize=STYLE['small_size'] - 0.5, labelpad=1.0)
    inner.set_ylabel('Elevation (ft)', fontsize=STYLE['small_size'] - 0.5, labelpad=1.0)
    inner.tick_params(axis='both', labelsize=STYLE['small_size'] - 1.0, length=2, pad=1.2, colors=STYLE['muted_text'])
    inner.xaxis.set_major_locator(MaxNLocator(nbins=8))
    inner.yaxis.set_major_locator(MaxNLocator(nbins=3))
    inner.yaxis.set_major_formatter(FuncFormatter(lambda v, _: f'{int(v):,}'))
    for spine in inner.spines.values():
        spine.set_color(STYLE['panel_rule'])
        spine.set_linewidth(0.38)


def icon_x_scale(ax):
    bbox = ax.get_position()
    fig_w, fig_h = ax.figure.get_size_inches()
    return (bbox.height * fig_h) / (bbox.width * fig_w)


def draw_icon_ellipse(ax, x, y, radius, **kwargs):
    xs = icon_x_scale(ax)
    ax.add_patch(Ellipse((x, y), width=radius * 2 * xs, height=radius * 2,
                         transform=ax.transAxes, **kwargs))


def draw_camp_icon(ax, x, y, size=0.030):
    """Draw a small tent-style campsite icon in axes coordinates."""
    xs = icon_x_scale(ax)
    tent = Polygon(
        [[x - size * 0.62 * xs, y - size * 0.42], [x, y + size * 0.62], [x + size * 0.62 * xs, y - size * 0.42]],
        closed=True, transform=ax.transAxes, facecolor=CAMP_COLOR,
        edgecolor='#fffdf6', lw=0.75, zorder=20,
    )
    door = Polygon(
        [[x - size * 0.16 * xs, y - size * 0.42], [x, y + size * 0.12], [x + size * 0.16 * xs, y - size * 0.42]],
        closed=True, transform=ax.transAxes, facecolor=STYLE['panel_face'],
        edgecolor='none', zorder=21,
    )
    ground = Rectangle(
        (x - size * 0.70 * xs, y - size * 0.50), size * 1.40 * xs, size * 0.08,
        transform=ax.transAxes, facecolor=CAMP_COLOR, edgecolor='none', zorder=19,
    )
    ax.add_patch(tent)
    ax.add_patch(door)
    ax.add_patch(ground)


def draw_poi_icon(ax, x, y, size=0.030):
    """Draw a point-of-interest map pin in axes coordinates."""
    xs = icon_x_scale(ax)
    draw_icon_ellipse(ax, x, y + size * 0.12, size * 0.40,
                      facecolor=POI_COLOR, edgecolor='#fffdf6', lw=0.75, zorder=20)
    ax.add_patch(Polygon(
        [[x - size * 0.24 * xs, y - size * 0.15], [x, y - size * 0.62], [x + size * 0.24 * xs, y - size * 0.15]],
        closed=True, transform=ax.transAxes, facecolor=POI_COLOR,
        edgecolor='#fffdf6', lw=0.55, zorder=19,
    ))
    draw_icon_ellipse(ax, x, y + size * 0.12, size * 0.145,
                      facecolor=STYLE['panel_face'], edgecolor='none', zorder=21)


def draw_water_icon(ax, x, y, size=0.030):
    """Draw a water droplet icon in axes coordinates."""
    xs = icon_x_scale(ax)
    ax.add_patch(Polygon(
        [
            [x, y + size * 0.68],
            [x - size * 0.42 * xs, y + size * 0.02],
            [x - size * 0.30 * xs, y - size * 0.40],
            [x, y - size * 0.62],
            [x + size * 0.30 * xs, y - size * 0.40],
            [x + size * 0.42 * xs, y + size * 0.02],
        ],
        closed=True, transform=ax.transAxes, facecolor=WATER_COLOR,
        edgecolor='#fffdf6', lw=0.75, zorder=20,
    ))


def draw_scenic_icon(ax, x, y, size=0.030):
    """Draw a sunburst-style scenic-view icon in axes coordinates."""
    xs = icon_x_scale(ax)
    for angle in np.linspace(0, 2 * np.pi, 12, endpoint=False):
        r0 = size * 0.30
        r1 = size * 0.62
        ax.plot(
            [x + np.cos(angle) * r0 * xs, x + np.cos(angle) * r1 * xs],
            [y + np.sin(angle) * r0, y + np.sin(angle) * r1],
            transform=ax.transAxes, color='#1f1c18', lw=0.75,
            solid_capstyle='round', zorder=19,
        )
    draw_icon_ellipse(ax, x, y, size * 0.24,
                      facecolor='#1f1c18', edgecolor='#fffdf6', lw=0.55, zorder=20)


def draw_symbol_icon(ax, icon, x, y, size=0.030):
    if icon == 'camp':
        draw_camp_icon(ax, x, y, size)
    elif icon == 'poi':
        draw_poi_icon(ax, x, y, size)
    elif icon == 'water':
        draw_water_icon(ax, x, y, size)
    elif icon == 'scenic':
        draw_scenic_icon(ax, x, y, size)


def draw_bottom_notes(ax):
    """Draw the bottom notes strip; adjust column spacing here."""
    draw_panel(ax)
    ax.text(0.025, 0.72, 'MAP NOTES', transform=ax.transAxes,
            fontsize=STYLE['section_size'], weight='bold', ha='left', color=STYLE['text'])
    ax.text(0.025, 0.42, 'Stay on trails. Protect natural and cultural resources.',
            transform=ax.transAxes, fontsize=STYLE['small_size'], ha='left', color=STYLE['text'])
    ax.text(0.025, 0.20, 'Leave No Trace. Carry water and check crew spacing.',
            transform=ax.transAxes, fontsize=STYLE['small_size'], ha='left', color=STYLE['text'])

    ax.plot([0.315, 0.315], [0.16, 0.82], transform=ax.transAxes, color=STYLE['panel_rule'], lw=STYLE['rule_lw'])
    ax.text(0.345, 0.72, 'SYMBOL REFERENCE', transform=ax.transAxes,
            fontsize=STYLE['section_size'], weight='bold', ha='left', color=STYLE['text'])
    entries = [
        (0.365, 0.43, 'camp', 'Camp'),
        (0.495, 0.43, 'poi', 'Point'),
        (0.625, 0.43, 'water', 'Water'),
        (0.365, 0.20, 'scenic', 'Scenic View'),
    ]
    for x, y, icon, label in entries:
        draw_symbol_icon(ax, icon, x, y, size=0.092)
        ax.text(x + 0.026, y, label, transform=ax.transAxes, va='center',
                fontsize=STYLE['small_size'], color=STYLE['text'])

    ax.plot([0.735, 0.735], [0.16, 0.82], transform=ax.transAxes, color=STYLE['panel_rule'], lw=STYLE['rule_lw'])
    ax.text(0.765, 0.72, 'GEOLOGY NOTE', transform=ax.transAxes,
            fontsize=STYLE['section_size'], weight='bold', ha='left', color=STYLE['text'])
    ax.text(0.765, 0.42, 'Geology units are generalized.',
            transform=ax.transAxes, fontsize=STYLE['small_size'], ha='left', color=STYLE['text'])
    ax.text(0.765, 0.20, 'Refer to full geologic map for details.',
            transform=ax.transAxes, fontsize=STYLE['small_size'], ha='left', color=STYLE['text'])


def draw_footer(ax):
    draw_bottom_notes(ax)


def draw_map_information(ax, trail_gdf, refs):
    draw_panel(ax, 'Map Information')
    info_rows = [
        ('Contour Interval', 'N/A'),
        ('Projection', 'UTM Zone 13N'),
        ('Datum', 'NAD83'),
        ('Sources', 'USGS, Philmont GIS'),
        ('Map Date', '2026'),
    ]
    y = 0.690
    for label, value in info_rows:
        ax.text(0.065, y, label, transform=ax.transAxes, fontsize=STYLE['small_size'] - 0.4,
                ha='left', va='center', color=STYLE['muted_text'])
        ax.text(0.430, y, value, transform=ax.transAxes, fontsize=STYLE['small_size'] - 0.4,
                ha='left', va='center', color=STYLE['text'])
        y -= 0.145
    inset = ax.inset_axes([0.705, 0.145, 0.235, 0.610])
    inset.set_facecolor('#e8e4d8')
    for spine in inset.spines.values():
        spine.set_color(STYLE['panel_edge'])
        spine.set_linewidth(0.45)
    try:
        if refs and 'Boundaries' in refs and not refs['Boundaries'].empty:
            refs['Boundaries'].plot(ax=inset, facecolor='none', edgecolor='#555', linewidth=0.7)
        trail_gdf.plot(ax=inset, color=TRAIL_COLOR, linewidth=1.1)
        centroid = trail_gdf.unary_union.centroid
        inset.plot(centroid.x, centroid.y, marker='o', color='#c51b1b', markersize=3)
        minx, miny, maxx, maxy = trail_gdf.total_bounds
        pad = max(maxx - minx, maxy - miny) * 5
        inset.set_xlim(minx - pad, maxx + pad)
        inset.set_ylim(miny - pad, maxy + pad)
    except Exception:
        inset.text(0.5, 0.5, 'Inset unavailable', ha='center', va='center', fontsize=7)
    inset.set_xticks([])
    inset.set_yticks([])


def draw_overview_inset(ax, trail_gdf, refs):
    draw_map_information(ax, trail_gdf, refs)


def plot_line_geometries(ax, gdf, color, linewidth, linestyle='-', alpha=1.0, zorder=10, rounded=False):
    if gdf is None or gdf.empty:
        return
    capstyle = 'round' if rounded else 'butt'
    joinstyle = 'round' if rounded else 'miter'
    for geom in gdf.geometry:
        if geom is None or geom.is_empty:
            continue
        geoms = list(geom.geoms) if geom.geom_type == 'MultiLineString' else [geom]
        for line in geoms:
            if not hasattr(line, 'xy'):
                continue
            xs, ys = line.xy
            ax.plot(xs, ys, color=color, linewidth=linewidth, linestyle=linestyle,
                    alpha=alpha, zorder=zorder, solid_capstyle=capstyle,
                    solid_joinstyle=joinstyle)


def draw_trail_mile_ticks(ax, trail_gdf, interval_miles=2):
    try:
        line = merge_line_geometry(trail_gdf)
    except Exception:
        return
    if line is None or line.is_empty or line.length <= interval_miles * 1609.344:
        return
    interval_m = interval_miles * 1609.344
    tick_len = max(line.length * 0.006, 55)
    distances = np.arange(interval_m, line.length, interval_m)
    for dist in distances:
        try:
            point = line.interpolate(float(dist))
            before = line.interpolate(max(float(dist) - 12, 0))
            after = line.interpolate(min(float(dist) + 12, line.length))
            dx = after.x - before.x
            dy = after.y - before.y
            norm = np.hypot(dx, dy)
            if norm == 0:
                continue
            nx = -dy / norm
            ny = dx / norm
            x0 = point.x - nx * tick_len / 2
            y0 = point.y - ny * tick_len / 2
            x1 = point.x + nx * tick_len / 2
            y1 = point.y + ny * tick_len / 2
            ax.plot([x0, x1], [y0, y1], color='#fffdf6', lw=2.3,
                    solid_capstyle='round', zorder=38)
            ax.plot([x0, x1], [y0, y1], color='#111111', lw=0.9,
                    solid_capstyle='round', zorder=39)
            label = f'{int(round(dist / 1609.344))} mi'
            txt = ax.annotate(
                label, xy=(point.x, point.y), xytext=(4, 4),
                textcoords='offset points', ha='left', va='bottom',
                fontsize=5.8, color='#111111', weight='bold',
                family=STYLE['font_family'], zorder=40,
            )
            txt.set_path_effects([
                PathEffects.withStroke(linewidth=2.0, foreground='#fffdf6'),
                PathEffects.Normal(),
            ])
        except Exception:
            continue


def split_points(points):
    empty = gpd.GeoDataFrame()
    if points is None or points.empty:
        return empty, empty, empty, empty
    text_cols = [col for col in points.columns if col.lower() in {'name', 'type', 'role', 'notes', 'description'}]
    if not text_cols:
        return points.iloc[0:0], points, points.iloc[0:0], points.iloc[0:0]
    text = points[text_cols].fillna('').astype(str).agg(' '.join, axis=1).str.lower()
    scenic_mask = text.str.contains(r'view|vista|overlook|peak|summit', regex=True)
    water_mask = text.str.contains(r'water|spring|creek|river|lake|pond', regex=True)
    camp_mask = text.str.contains(r'camp|trailhead|base|staff|overnight|layover', regex=True) & ~water_mask
    camps = points[camp_mask]
    waters = points[water_mask & ~scenic_mask]
    scenic = points[scenic_mask]
    pois = points[~camp_mask & ~water_mask & ~scenic_mask]
    return camps, pois, waters, scenic


def read_daily_segments(segment_paths, target_crs: str) -> gpd.GeoDataFrame:
    segments = []
    for shp in segment_paths:
        try:
            gdf = gpd.read_file(shp)
            if gdf.empty:
                warnings.warn(f'{shp} is empty; skipping overview segment')
                continue
            if gdf.crs is None:
                warnings.warn(f'CRS undefined for {shp}; skipping overview segment')
                continue
            gdf = gdf.to_crs(target_crs)
            gdf['day_num'] = parse_day_from_name(shp)
            gdf['day_label'] = pretty_day_label(shp)
            segments.append(gdf)
        except Exception as exc:
            warnings.warn(f'Could not read overview segment {shp}: {exc}')
    if not segments:
        return gpd.GeoDataFrame()
    return gpd.GeoDataFrame(pd.concat(segments, ignore_index=True), crs=target_crs)


def read_geology_cross_sections(section_path: Path, target_crs: str) -> gpd.GeoDataFrame:
    if not section_path:
        return gpd.GeoDataFrame()
    section_path = Path(section_path)
    if not section_path.exists():
        warnings.warn(f'Geology cross-section shapefile not found: {section_path}')
        return gpd.GeoDataFrame()
    try:
        sections = gpd.read_file(section_path)
    except Exception as exc:
        warnings.warn(f'Could not read geology cross-section shapefile {section_path}: {exc}')
        return gpd.GeoDataFrame()
    if sections.empty:
        warnings.warn(f'Geology cross-section shapefile is empty: {section_path}')
        return gpd.GeoDataFrame()
    if sections.crs is None:
        warnings.warn(f'CRS undefined for geology cross-section shapefile {section_path}; skipping')
        return gpd.GeoDataFrame()
    try:
        return sections.to_crs(target_crs)
    except Exception as exc:
        warnings.warn(f'Could not reproject geology cross-section shapefile {section_path}: {exc}')
        return gpd.GeoDataFrame()


def draw_cross_section_panel(ax, image_path: Path):
    draw_panel(ax)
    ax.text(0.018, 0.930, "GEOLOGIC CROSS SECTIONS A-A' AND B-B'",
            transform=ax.transAxes, ha='left', va='top',
            fontsize=STYLE['section_size'], weight='bold',
            color=STYLE['text'], family=STYLE['font_family'])
    ax.plot([0.018, 0.982], [0.855, 0.855], transform=ax.transAxes,
            color=STYLE['panel_rule'], lw=STYLE['rule_lw'])
    if image_path and Path(image_path).exists():
        try:
            img = plt.imread(str(image_path))
            ax.imshow(img, extent=(0.018, 0.982, 0.060, 0.815),
                      transform=ax.transAxes, aspect='auto', zorder=2)
            return
        except Exception as exc:
            warnings.warn(f'Could not read cross-section image {image_path}: {exc}')
    ax.text(0.500, 0.420, 'Cross-section image unavailable',
            transform=ax.transAxes, ha='center', va='center',
            fontsize=STYLE['body_size'], color=STYLE['muted_text'],
            family=STYLE['font_family'])


def draw_overview_legend(ax, day_entries, show_cross_sections=False, total_distance_miles=None):
    draw_panel(ax, 'All Segments')
    total_text = f'Total Distance: {total_distance_miles:.1f} mi' if total_distance_miles is not None else 'Total Distance: N/A'
    ax.text(0.075, 0.790, total_text, transform=ax.transAxes,
            ha='left', va='center', fontsize=STYLE['small_size'] + 0.35,
            color=STYLE['text'], weight='bold', family=STYLE['font_family'])
    ax.plot([0.060, 0.940], [0.735, 0.735], transform=ax.transAxes,
            color=STYLE['panel_rule'], lw=STYLE['rule_lw'])
    y = 0.670
    y_step = 0.047
    for day_num, label, color in day_entries:
        short_label = re.sub(r'^Day\s+0*\d+\s*[-–—]\s*', '', label).strip()
        if len(short_label) > 19:
            short_label = short_label[:18] + '...'
        ax.plot([0.075, 0.185], [y, y], transform=ax.transAxes,
                color=color, lw=STYLE['legend_line_width'] + 0.7,
                solid_capstyle='round')
        ax.text(0.220, y, f'Day {day_num}: {short_label}', transform=ax.transAxes,
                ha='left', va='center', fontsize=STYLE['small_size'] - 0.35,
                color=STYLE['text'], family=STYLE['font_family'])
        y -= y_step
    if show_cross_sections:
        ax.plot([0.075, 0.185], [0.150, 0.150], transform=ax.transAxes,
                color='#1f1c18', lw=2.2, linestyle=(0, (5, 2)),
                solid_capstyle='round')
        ax.text(0.220, 0.150, 'Geology Cross Section', transform=ax.transAxes,
                ha='left', va='center', fontsize=STYLE['small_size'] - 0.35,
                color=STYLE['text'], family=STYLE['font_family'])
    ax.plot([0.060, 0.940], [0.095, 0.095], transform=ax.transAxes,
            color=STYLE['panel_rule'], lw=STYLE['rule_lw'])
    ax.text(0.075, 0.050, 'Topo, reference trails, roads, water, POIs, and geology section traces are shown for route context.',
            transform=ax.transAxes, ha='left', va='center', wrap=True,
            fontsize=STYLE['small_size'] - 0.45, color=STYLE['muted_text'],
            family=STYLE['font_family'])


def create_combined_overview_map(segment_paths, topo_raster: Path, geology_raster: Path, dem_raster: Path, points_gdf: gpd.GeoDataFrame, refs: dict[str, gpd.GeoDataFrame], out_pdf: Path, buffer_m=1500, cross_sections_gdf=None, cross_section_image=None, overview_profile_image=None):
    trails = read_daily_segments(segment_paths, TARGET_CRS)
    if trails.empty:
        raise ValueError('No valid daily segments available for combined overview map')

    extent_geom = get_buffered_extent(trails, buffer_m)
    topo_img = topo_transform = None
    if topo_raster:
        topo_img, topo_transform, topo_crs = reproject_and_clip_raster(topo_raster, TARGET_CRS, extent_geom)
    dem_img = dem_transform = None
    if dem_raster:
        try:
            dem_img, dem_transform, dem_crs = reproject_and_clip_raster(dem_raster, TARGET_CRS, extent_geom)
        except Exception as exc:
            warnings.warn(f'Could not process DEM raster for overview map: {exc}')
    geo_img = geo_transform = None
    if geology_raster:
        try:
            geo_img, geo_transform, geo_crs = reproject_and_clip_raster(geology_raster, TARGET_CRS, extent_geom)
        except Exception as exc:
            warnings.warn(f'Could not process geology raster for overview map: {exc}')

    try:
        points = points_gdf.clip(extent_geom) if points_gdf is not None and not points_gdf.empty else gpd.GeoDataFrame()
    except Exception:
        points = gpd.GeoDataFrame()
    try:
        cross_sections = cross_sections_gdf.clip(extent_geom) if cross_sections_gdf is not None and not cross_sections_gdf.empty else gpd.GeoDataFrame()
    except Exception:
        cross_sections = gpd.GeoDataFrame()
    total_distance_miles = trails.length.sum() / 1609.344 if not trails.empty else None
    full_profile = sample_full_trip_profile(trails, dem_img, dem_transform)

    fig = plt.figure(figsize=PAGE_SIZE, facecolor=STYLE['page_face'])
    header_ax = fig.add_axes(LAYOUT['header'])
    map_ax = fig.add_axes([0.014, 0.430, 0.720, 0.455])
    legend_ax = fig.add_axes([0.760, 0.430, 0.226, 0.455])
    full_profile_ax = fig.add_axes([0.176, 0.286, 0.648, 0.120])
    cross_section_ax = fig.add_axes([0.176, 0.026, 0.648, 0.235])

    def raster_extent(transform, arr_shape):
        cols = arr_shape[2]
        rows = arr_shape[1]
        left = transform[2]
        top = transform[5]
        right = left + transform[0] * cols
        bottom = top + transform[4] * rows
        return (left, right, bottom, top)

    map_ax.set_facecolor('#e8dfcc')
    if topo_img is not None:
        topo_ext = raster_extent(topo_transform, topo_img.shape)
        if topo_img.shape[0] >= 3:
            map_ax.imshow(soften_rgb_image(topo_img.transpose(1, 2, 0)), extent=topo_ext, alpha=TOPO_ALPHA, zorder=1)
        else:
            map_ax.imshow(topo_img[0], cmap='terrain', extent=topo_ext, alpha=TOPO_ALPHA, zorder=1)
        if dem_img is not None and dem_transform is not None:
            hillshade = calculate_hillshade(dem_img, dem_transform)
            map_ax.imshow(hillshade, cmap='gray', extent=topo_ext, alpha=HILLSHADE_ALPHA, zorder=2)
    if geo_img is not None:
        geo_ext = raster_extent(geo_transform, geo_img.shape)
        if geo_img.shape[0] >= 3:
            map_ax.imshow(geo_img.transpose(1, 2, 0), extent=geo_ext, alpha=GEOLOGY_ALPHA, zorder=3)
        else:
            map_ax.imshow(geo_img[0], cmap='tab20c', extent=geo_ext, alpha=GEOLOGY_ALPHA, zorder=3)

    if refs:
        for key, layer in refs.items():
            if layer.empty:
                continue
            try:
                clipped_layer = layer.clip(extent_geom)
            except Exception:
                continue
            if clipped_layer.empty:
                continue
            style = reference_layer_style(key)
            geom_kind = clipped_layer.geom_type.iloc[0].lower()
            if geom_kind.endswith('polygon'):
                clipped_layer.plot(ax=map_ax, facecolor=style.get('facecolor', 'none'),
                                   edgecolor=style.get('edgecolor', '#333333'),
                                   linewidth=style.get('linewidth', 1),
                                   alpha=style.get('alpha', 0.18), zorder=4)
            elif geom_kind.endswith('linestring'):
                plot_line_geometries(map_ax, clipped_layer, color=style.get('color', '#444444'),
                                     linewidth=style.get('linewidth', 1.0), alpha=style.get('alpha', 0.52),
                                     linestyle=style.get('linestyle', '-'), zorder=12)
            else:
                clipped_layer.plot(ax=map_ax, marker=style.get('marker', 'x'),
                                   color=style.get('color', '#444444'),
                                   markersize=style.get('markersize', 24),
                                   alpha=style.get('alpha', 0.75), zorder=18)

    cmap = plt.colormaps.get_cmap('tab20')
    day_entries = []
    for idx, (day_num, day_group) in enumerate(trails.groupby('day_num', sort=True)):
        color = cmap(idx % 20)
        label = day_group['day_label'].iloc[0]
        day_entries.append((day_num, label, color))
        plot_line_geometries(map_ax, day_group, color='white', linewidth=OVERVIEW_TRAIL_CASING_WIDTH, zorder=29, rounded=True)
        plot_line_geometries(map_ax, day_group, color=color, linewidth=OVERVIEW_TRAIL_WIDTH, zorder=30, rounded=True)
        try:
            line = merge_line_geometry(day_group)
            mid = line.interpolate(line.length * 0.52)
            txt = map_ax.text(mid.x, mid.y, str(day_num), ha='center', va='center',
                              fontsize=7.5, weight='bold', color='white', zorder=42,
                              family=STYLE['font_family'],
                              bbox=dict(boxstyle='circle,pad=0.22', facecolor=color,
                                        edgecolor='white', linewidth=0.7))
            txt.set_path_effects([PathEffects.withStroke(linewidth=1.1, foreground='#1c1a16'), PathEffects.Normal()])
        except Exception:
            pass

    if points is not None and not points.empty and 'feature_na' in points.columns:
        points.plot(ax=map_ax, marker=POI_MARKER, color=POI_COLOR, edgecolor='white',
                    linewidth=0.45, markersize=30, zorder=36)
        draw_labels(map_ax, points, label_field='feature_na', fontsize=5.6, xytext=(3, 2), halo_width=2.4)
    if cross_sections is not None and not cross_sections.empty:
        plot_line_geometries(map_ax, cross_sections, color='white', linewidth=4.2,
                             linestyle=(0, (5, 2)), alpha=0.95, zorder=43, rounded=True)
        plot_line_geometries(map_ax, cross_sections, color='#1f1c18', linewidth=2.0,
                             linestyle=(0, (5, 2)), alpha=0.95, zorder=44, rounded=True)
        label_field = 'Section' if 'Section' in cross_sections.columns else None
        for _, row in cross_sections.iterrows():
            geom = row.geometry
            if geom is None or geom.is_empty:
                continue
            line = max(list(geom.geoms), key=lambda g: g.length) if geom.geom_type == 'MultiLineString' else geom
            try:
                start = line.interpolate(0)
                end = line.interpolate(line.length)
                section = str(row[label_field]).strip() if label_field and row[label_field] else ''
                labels = [(start, section or 'Section', 'right'), (end, f"{section}'" if section else '', 'left')]
                for point, text, ha in labels:
                    if not text:
                        continue
                    txt = map_ax.text(point.x, point.y, text, ha=ha, va='center',
                                      fontsize=7.2, weight='bold', color='#1f1c18',
                                      zorder=45, family=STYLE['font_family'])
                    txt.set_path_effects([
                        PathEffects.withStroke(linewidth=2.4, foreground='#fffdf6'),
                        PathEffects.Normal(),
                    ])
            except Exception:
                continue

    minx, miny, maxx, maxy = extent_geom.bounds
    map_ax.set_xlim(minx, maxx)
    map_ax.set_ylim(miny, maxy)
    map_ax.set_aspect('equal', adjustable='box')
    map_ax.set_xticks([])
    map_ax.set_yticks([])
    for spine in map_ax.spines.values():
        spine.set_color(PANEL_EDGE)
        spine.set_linewidth(1.0)
    draw_custom_north_arrow(map_ax, x=0.952, y=0.905, size=0.050)
    draw_custom_scale_bar(map_ax, length_m=3218.688, location=(0.035, 0.035))
    draw_header(header_ax, 'Trek 12-15 | All Daily Segments', 'Combined overview map | Philmont Scout Ranch')
    draw_overview_legend(legend_ax, day_entries, show_cross_sections=not cross_sections.empty, total_distance_miles=total_distance_miles)
    draw_full_trip_profile(full_profile_ax, full_profile, overview_profile_image)
    draw_cross_section_panel(cross_section_ax, cross_section_image)

    out_pdf.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(out_pdf, dpi=300, facecolor=fig.get_facecolor())
    plt.close(fig)


def _legacy_create_map_for_day(day_shp: Path, topo_raster: Path, geology_raster: Path, dem_raster: Path, points_gdf: gpd.GeoDataFrame, refs: dict[str, gpd.GeoDataFrame], out_pdf: Path, buffer_m=1000):
    # Read trail
    trail = gpd.read_file(day_shp)
    if trail.empty:
        warnings.warn(f'{day_shp} is empty; skipping')
        return
    if trail.crs is None:
        raise ValueError(f'CRS undefined for {day_shp}')
    trail = trail.to_crs(TARGET_CRS)

    extent_geom = get_buffered_extent(trail, buffer_m)

    # Prepare rasters (warp + clip)
    topo_img, topo_transform, topo_crs = reproject_and_clip_raster(topo_raster, TARGET_CRS, extent_geom)
    dem_img = None
    dem_transform = None
    if dem_raster:
        try:
            dem_img, dem_transform, dem_crs = reproject_and_clip_raster(dem_raster, TARGET_CRS, extent_geom)
        except Exception as e:
            warnings.warn(f'Could not process DEM raster: {e}')
    geo_img = None
    geo_transform = None
    if geology_raster:
        try:
            geo_img, geo_transform, geo_crs = reproject_and_clip_raster(geology_raster, TARGET_CRS, extent_geom)
        except Exception as e:
            warnings.warn(f'Could not process geology raster: {e}')

    # Clip points to extent
    if not points_gdf.empty:
        points = points_gdf.clip(extent_geom)
    else:
        points = gpd.GeoDataFrame()

    # Matplotlib figure
    fig = plt.figure(figsize=PAGE_SIZE)
    ax = fig.add_axes([0.06, 0.12, 0.88, 0.72])  # main map area

    # Plot topo: rasterio returns array (bands, rows, cols)
    def raster_extent(transform, arr_shape):
        cols = arr_shape[2]
        rows = arr_shape[1]
        left = transform[2]
        top = transform[5]
        right = left + transform[0] * cols
        bottom = top + transform[4] * rows
        return (left, right, bottom, top)

    if topo_img is not None:
        topo_arr = topo_img
        topo_ext = raster_extent(topo_transform, topo_arr.shape)
        if dem_img is not None and dem_transform is not None:
            hillshade = calculate_hillshade(dem_img, dem_transform)
            ax.imshow(hillshade, cmap='gray', extent=topo_ext, alpha=HILLSHADE_ALPHA, zorder=1)
        if topo_arr.shape[0] >= 3:
            # RGB
            img = topo_arr.transpose(1, 2, 0)
            ax.imshow(img, extent=topo_ext, alpha=TOPO_ALPHA, zorder=2)
        else:
            ax.imshow(topo_arr[0], cmap='terrain', extent=topo_ext, alpha=TOPO_ALPHA, zorder=2)

    # Geology overlay
    if geo_img is not None:
        geo_ext = raster_extent(geo_transform, geo_img.shape)
        if geo_img.shape[0] >= 3:
            ax.imshow(geo_img.transpose(1, 2, 0), extent=geo_ext, alpha=GEOLOGY_ALPHA, zorder=3)
        else:
            ax.imshow(geo_img[0], cmap='tab20', extent=geo_ext, alpha=GEOLOGY_ALPHA, zorder=3)

    # Reference layers
    if refs:
        for key, layer in refs.items():
            if layer.empty:
                continue
            clipped_layer = layer.clip(extent_geom)
            if clipped_layer.empty:
                continue
            style = reference_layer_style(key)
            if clipped_layer.geom_type.iloc[0].lower().endswith('polygon'):
                clipped_layer.plot(ax=ax, facecolor=style.get('facecolor', 'none'), edgecolor=style.get('edgecolor', '#333333'), linewidth=style.get('linewidth', 1), alpha=style.get('alpha', 0.4), label=style['label'])
            elif clipped_layer.geom_type.iloc[0].lower().endswith('linestring'):
                clipped_layer.plot(ax=ax, color=style.get('color', '#444444'), linewidth=style.get('linewidth', 1.2), alpha=style.get('alpha', 0.7), linestyle=style.get('linestyle', '-'), label=style['label'])
            else:
                clipped_layer.plot(ax=ax, marker=style.get('marker', 'x'), color=style.get('color', '#444444'), markersize=style.get('markersize', 30), alpha=style.get('alpha', 0.9), label=style['label'])
            if style.get('label_points') and 'Name' in clipped_layer.columns:
                draw_labels(ax, clipped_layer, label_field='Name')

    # Plot trail
    trail.plot(ax=ax, color=TRAIL_COLOR, linewidth=TRAIL_WIDTH)

    # Camps and POIs
    if not points.empty:
        # Attempt to separate camps by attribute name heuristics
        name_col = 'name' if 'name' in points.columns else 'Name' if 'Name' in points.columns else points.columns[0]
        points.plot(ax=ax, marker=POI_MARKER, color=POI_COLOR, markersize=40, label='POI')
        draw_labels(ax, points, label_field=name_col)

    # Labels for trail endpoints or camps could be added here

    # Map decorations
    draw_north_arrow(ax)
    draw_scale_bar(ax)

    # Legend
    handles = []
    from matplotlib.lines import Line2D
    handles.append(Line2D([0], [0], color=TRAIL_COLOR, lw=3, label='Daily Trail'))
    handles.append(Line2D([0], [0], marker=CAMP_MARKER, color='w', markerfacecolor=CAMP_COLOR, markersize=8, label='Camps'))
    handles.append(Line2D([0], [0], marker=POI_MARKER, color='w', markerfacecolor=POI_COLOR, markersize=6, label='POI'))
    if refs and 'Roads' in refs and not refs['Roads'].empty:
        handles.append(Line2D([0], [0], color='#8c4b00', lw=2, label='Reference Roads'))
    if refs and 'Trails' in refs and not refs['Trails'].empty:
        handles.append(Line2D([0], [0], color='#2a8f4f', lw=2, linestyle='--', label='Reference Trails'))
    if refs and 'Wildfires' in refs and not refs['Wildfires'].empty:
        handles.append(Line2D([0], [0], color='#b22a2a', lw=6, alpha=0.3, label='Wildfire'))
    if refs and 'Boundaries' in refs and not refs['Boundaries'].empty:
        handles.append(Line2D([0], [0], color='#666666', lw=1, label='Reference Boundaries'))
    ax.legend(handles=handles, fontsize=9, loc='upper right')

    # Title
    title = f'Trek 12-15 — {day_shp.stem}'
    ax.set_title(title, fontsize=14)

    # Text box area
    info_text = 'Mileage: N/A\nElevation gain/loss: N/A\nStart: N/A\nEnd: N/A\nNotes: Generated by generate_daily_maps.py'
    fig.text(0.06, 0.02, info_text, fontsize=9)

    # Hide axis ticks
    ax.set_xticks([])
    ax.set_yticks([])

    # Save single-page PDF
    out_pdf.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(out_pdf, dpi=300)
    plt.close(fig)


def create_map_for_day(day_shp: Path, topo_raster: Path, geology_raster: Path, dem_raster: Path, points_gdf: gpd.GeoDataFrame, refs: dict[str, gpd.GeoDataFrame], out_pdf: Path, buffer_m=1000, daily_hiking_stats=None, daily_content=None):
    # Read trail
    trail = gpd.read_file(day_shp)
    if trail.empty:
        warnings.warn(f'{day_shp} is empty; skipping')
        return
    if trail.crs is None:
        raise ValueError(f'CRS undefined for {day_shp}')
    trail = trail.to_crs(TARGET_CRS)

    extent_geom = get_buffered_extent(trail, buffer_m)
    map_extent_geom = expand_extent_to_panel_aspect(extent_geom, LAYOUT['map'], PAGE_SIZE)

    # Prepare rasters for the full map panel extent, not the tighter route buffer.
    topo_img = topo_transform = None
    if topo_raster:
        topo_img, topo_transform, topo_crs = reproject_and_clip_raster(topo_raster, TARGET_CRS, map_extent_geom)
    dem_img = None
    dem_transform = None
    if dem_raster:
        try:
            dem_img, dem_transform, dem_crs = reproject_and_clip_raster(dem_raster, TARGET_CRS, map_extent_geom)
        except Exception as e:
            warnings.warn(f'Could not process DEM raster: {e}')
    geo_img = None
    geo_transform = None
    if geology_raster:
        try:
            geo_img, geo_transform, geo_crs = reproject_and_clip_raster(geology_raster, TARGET_CRS, map_extent_geom)
        except Exception as e:
            warnings.warn(f'Could not process geology raster: {e}')

    if points_gdf is not None and not points_gdf.empty:
        try:
            points = points_gdf.clip(map_extent_geom)
        except Exception:
            points = gpd.GeoDataFrame()
    else:
        points = gpd.GeoDataFrame()

    day_num = parse_day_from_name(day_shp)
    day_label = pretty_day_label(day_shp)
    meta = read_day_yaml_metadata(day_num)
    if daily_hiking_stats and day_num in daily_hiking_stats:
        meta.update(daily_hiking_stats[day_num])
    daily_content = daily_content or {}
    profile = sample_elevation_profile(trail, dem_img, dem_transform)
    profile = adjust_daily_profile(day_num, profile, meta)

    fig = plt.figure(figsize=PAGE_SIZE, facecolor=STYLE['page_face'])
    header_ax = fig.add_axes(LAYOUT['header'])
    main_ax = fig.add_axes(LAYOUT['map'])
    legend_ax = fig.add_axes(LAYOUT['legend'])
    geology_ax = fig.add_axes(LAYOUT['geology'])
    summary_ax = fig.add_axes(LAYOUT['summary'])
    profile_ax = fig.add_axes(LAYOUT['profile'])
    geology_note_ax = fig.add_axes(LAYOUT['geology_note'])
    riddle_ax = fig.add_axes(LAYOUT['riddle'])
    look_for_this_ax = fig.add_axes(LAYOUT['look_for_this'])
    astronomy_ax = fig.add_axes(LAYOUT['astronomy'])

    def raster_extent(transform, arr_shape):
        cols = arr_shape[2]
        rows = arr_shape[1]
        left = transform[2]
        top = transform[5]
        right = left + transform[0] * cols
        bottom = top + transform[4] * rows
        return (left, right, bottom, top)

    main_ax.set_facecolor('#e8dfcc')
    if topo_img is not None:
        topo_arr = topo_img
        topo_ext = raster_extent(topo_transform, topo_arr.shape)
        if topo_arr.shape[0] >= 3:
            main_ax.imshow(soften_rgb_image(topo_arr.transpose(1, 2, 0)), extent=topo_ext, alpha=TOPO_ALPHA, zorder=1)
        else:
            main_ax.imshow(topo_arr[0], cmap='terrain', extent=topo_ext, alpha=TOPO_ALPHA, zorder=1)
        if dem_img is not None and dem_transform is not None:
            hillshade = calculate_hillshade(dem_img, dem_transform)
            main_ax.imshow(hillshade, cmap='gray', extent=topo_ext, alpha=HILLSHADE_ALPHA, zorder=2)

    if geo_img is not None:
        geo_ext = raster_extent(geo_transform, geo_img.shape)
        if geo_img.shape[0] >= 3:
            main_ax.imshow(geo_img.transpose(1, 2, 0), extent=geo_ext, alpha=GEOLOGY_ALPHA, zorder=3)
        else:
            main_ax.imshow(geo_img[0], cmap='tab20c', extent=geo_ext, alpha=GEOLOGY_ALPHA, zorder=3)

    if refs:
        for key, layer in refs.items():
            if layer.empty:
                continue
            try:
                clipped_layer = layer.clip(map_extent_geom)
            except Exception:
                continue
            if clipped_layer.empty:
                continue
            style = reference_layer_style(key)
            geom_kind = clipped_layer.geom_type.iloc[0].lower()
            if geom_kind.endswith('polygon'):
                clipped_layer.plot(
                    ax=main_ax,
                    facecolor=style.get('facecolor', 'none'),
                    edgecolor=style.get('edgecolor', '#333333'),
                    linewidth=style.get('linewidth', 1),
                    alpha=style.get('alpha', 0.22),
                    zorder=4,
                )
            elif geom_kind.endswith('linestring'):
                plot_line_geometries(
                    main_ax, clipped_layer,
                    color=style.get('color', '#444444'),
                    linewidth=style.get('linewidth', 1.2),
                    alpha=style.get('alpha', 0.70),
                    linestyle=style.get('linestyle', '-'),
                    zorder=12,
                )
            else:
                clipped_layer.plot(
                    ax=main_ax,
                    marker=style.get('marker', 'x'),
                    color=style.get('color', '#444444'),
                    markersize=style.get('markersize', 30),
                    alpha=style.get('alpha', 0.9),
                    zorder=18,
                )
            if style.get('label_points') and 'Name' in clipped_layer.columns:
                draw_labels(main_ax, clipped_layer, label_field='Name', fontsize=6.7, xytext=(4, 3), halo_width=2.7)

    plot_line_geometries(main_ax, trail, color='white', linewidth=DAILY_TRAIL_CASING_WIDTH, zorder=28, rounded=True)
    plot_line_geometries(main_ax, trail, color=TRAIL_COLOR, linewidth=DAILY_TRAIL_WIDTH, zorder=29, rounded=True)

    if not points.empty:
        if 'feature_na' in points.columns:
            points.plot(ax=main_ax, marker=POI_MARKER, color=POI_COLOR, edgecolor='white',
                        linewidth=0.5, markersize=45, zorder=31)
            draw_labels(main_ax, points, label_field='feature_na', fontsize=6.7, xytext=(4, 3), halo_width=2.7)
        else:
            name_col = 'name' if 'name' in points.columns else 'Name' if 'Name' in points.columns else points.columns[0]
            camps, pois, waters, scenic = split_points(points)
            if not pois.empty:
                pois.plot(ax=main_ax, marker=POI_MARKER, color=POI_COLOR, edgecolor='white',
                          linewidth=0.5, markersize=45, zorder=31)
                draw_labels(main_ax, pois, label_field=name_col, fontsize=6.7, xytext=(4, 3), halo_width=2.7)
            if not waters.empty:
                waters.plot(ax=main_ax, marker='o', color=WATER_COLOR, edgecolor='white',
                            linewidth=0.7, markersize=48, zorder=32)
                draw_labels(
                    main_ax, waters, label_field=name_col, fontsize=6.9, color=WATER_COLOR,
                    style='italic', xytext=(5, -1), halo_width=3.0, va='center'
                )
            if not scenic.empty:
                scenic.plot(ax=main_ax, marker=(12, 1, 0), color='#222222', edgecolor='white',
                            linewidth=0.5, markersize=80, zorder=33)
                draw_labels(
                    main_ax, scenic, label_field=name_col, fontsize=6.8, style='italic',
                    xytext=(6, -3), halo_width=2.9, va='center'
                )
            if not camps.empty:
                camps.plot(ax=main_ax, marker=CAMP_MARKER, color=CAMP_COLOR, edgecolor='white',
                           linewidth=0.8, markersize=90, zorder=34)
                draw_labels(
                    main_ax, camps, label_field=name_col, fontsize=7.9, weight='bold',
                    xytext=(6, 2), halo_width=3.4, va='center'
                )

    minx, miny, maxx, maxy = map_extent_geom.bounds
    main_ax.set_xlim(minx, maxx)
    main_ax.set_ylim(miny, maxy)
    main_ax.set_aspect('equal', adjustable='box')
    main_ax.set_xticks([])
    main_ax.set_yticks([])
    for spine in main_ax.spines.values():
        spine.set_color(PANEL_EDGE)
        spine.set_linewidth(1.0)

    draw_custom_north_arrow(main_ax)
    draw_custom_scale_bar(main_ax)
    header_subtitle = route_subtitle(day_num, day_label, meta, daily_content)
    header_note = extract_water_camping_note(meta.get('program'))
    header_quote = daily_content.get('clone_quote', {}).get(day_num, '')
    header_scout_skill = daily_content.get('scout_skill', {}).get(day_num, '')
    draw_header(header_ax, f'Trek 12-15 | {day_label}', header_subtitle, header_note, header_quote, header_scout_skill)
    draw_custom_legend(legend_ax)
    draw_geology_key(geology_ax)
    draw_trail_summary(summary_ax, day_label, trail, meta, profile)
    draw_elevation_profile(profile_ax, profile)
    draw_daily_text_panel(
        geology_note_ax, 'Geology', daily_content.get('geology', {}).get(day_num),
        fill_panel=True, icon_key='geology'
    )
    draw_daily_text_panel(
        riddle_ax, 'Daily Riddle', daily_content.get('riddle', {}).get(day_num),
        fill_panel=True, icon_key='riddle'
    )
    draw_daily_text_panel(
        look_for_this_ax, 'Look For This Today', daily_content.get('look_for_this', {}).get(day_num),
        fill_panel=True, icon_key='look'
    )
    draw_daily_text_panel(
        astronomy_ax, 'Astronomy', daily_content.get('astronomy', {}).get(day_num),
        fill_panel=True, icon_key='astronomy'
    )

    out_pdf.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(out_pdf, dpi=300, facecolor=fig.get_facecolor())
    plt.close(fig)


def read_kml_or_kmz(path: Path) -> gpd.GeoDataFrame:
    if path.suffix.lower() == '.kmz':
        import zipfile
        import io
        with zipfile.ZipFile(path, 'r') as z:
            kml_names = [name for name in z.namelist() if name.lower().endswith('.kml')]
            if not kml_names:
                raise FileNotFoundError(f'No KML file found inside {path}')
            with z.open(kml_names[0]) as kml_file:
                data = kml_file.read()
                return gpd.read_file(io.BytesIO(data))
    return gpd.read_file(str(path))


def load_reference_layers(reference_dir: Path, target_crs: str) -> dict[str, gpd.GeoDataFrame]:
    ref_dir = Path(reference_dir)
    layers = {}
    if not ref_dir.exists():
        warnings.warn(f'Reference directory not found: {ref_dir}')
        return layers
    for path in sorted(ref_dir.glob('*')):
        if path.suffix.lower() not in {'.kml', '.kmz'}:
            continue
        try:
            layer = read_kml_or_kmz(path)
            if layer.empty:
                continue
            if layer.crs is None:
                layer.set_crs('EPSG:4326', inplace=True)
            layer = layer.to_crs(target_crs)
            key = path.stem
            layers[key] = layer
        except Exception as exc:
            warnings.warn(f'Could not load reference layer {path}: {exc}')
    return layers


def reference_layer_style(layer_name: str) -> dict:
    key = layer_name.lower()
    if 'wildfire' in key:
        return {'facecolor': '#d94701', 'edgecolor': '#a32b1d', 'alpha': 0.25, 'linewidth': 0.8, 'label': 'Wildfires'}
    if 'roads' in key:
        return {'color': ROAD_COLOR, 'linewidth': 1.35, 'alpha': 0.78, 'linestyle': '-', 'label': 'Reference Roads'}
    if 'trails' in key:
        return {'color': REFERENCE_TRAIL_COLOR, 'linewidth': 1.45, 'alpha': 0.78, 'linestyle': (0, (4, 3)), 'label': 'Reference Trails'}
    if 'water' in key or 'hydro' in key or 'stream' in key:
        return {'color': WATER_COLOR, 'linewidth': 1.15, 'alpha': 0.78, 'linestyle': '--', 'label': 'Water'}
    if 'boundaries' in key:
        return {'color': '#666666', 'linewidth': 1.0, 'alpha': 0.8, 'linestyle': ':', 'label': 'Reference Boundaries'}
    if 'camps' in key:
        return {'marker': CAMP_MARKER, 'color': CAMP_COLOR, 'markersize': 60, 'alpha': 0.9, 'label': 'Reference Camps', 'label_points': True}
    if 'feature' in key:
        return {'marker': POI_MARKER, 'color': POI_COLOR, 'markersize': 45, 'alpha': 0.85, 'label': 'Reference Features', 'label_points': True}
    return {'color': '#444444', 'linewidth': 1.2, 'alpha': 0.7, 'label': layer_name}


def collect_point_gdfs(points_dir: Path, target_crs: str) -> gpd.GeoDataFrame:
    p = Path(points_dir)
    if not p.exists():
        return gpd.GeoDataFrame()
    gdfs = []
    for shp in p.glob('*.shp'):
        try:
            g = gpd.read_file(shp)
            if g.crs is None:
                warnings.warn(f'CRS undefined for {shp}; skipping')
                continue
            g = g.to_crs(target_crs)
            gdfs.append(g)
        except Exception as e:
            warnings.warn(f'Error reading {shp}: {e}')
    if not gdfs:
        return gpd.GeoDataFrame()
    df = pd.concat(gdfs, ignore_index=True)
    return gpd.GeoDataFrame(df).set_crs(target_crs)


def main():
    parser = argparse.ArgumentParser(description='Generate daily printable maps as PDFs')
    parser.add_argument('--daily-segments', required=True, help='Folder with daily segment shapefiles')
    parser.add_argument('--topo', required=False, help='Topographic raster (tif)')
    parser.add_argument('--dem', required=False, help='DEM raster for hillshade (tif)')
    parser.add_argument('--geology', required=False, help='Geology raster (tif) optional')
    parser.add_argument('--references', required=False, default='References/GIS/Web_Data_2023', help='KML/KMZ reference layer directory')
    parser.add_argument('--buffer', type=float, default=1000, help='Buffer around trail in meters')
    parser.add_argument('--outdir', default='DailyGuide/DailyMaps', help='Output directory for PDFs')
    parser.add_argument('--combined', action='store_true', help='Produce a combined PDF')
    parser.add_argument('--combined-overview', action='store_true', help='Produce one overview PDF with all daily segments on one map')
    parser.add_argument('--combined-overview-name', default='DailyMaps_all_segments.pdf', help='Filename for the all-segments overview PDF')
    parser.add_argument(
        '--geology-cross-sections',
        default='References/GIS/Geology_Sections/CrossSections.shp',
        help='Geology cross-section trace shapefile to draw on the combined overview map',
    )
    parser.add_argument(
        '--cross-section-image',
        default='References/GIS/Geology_Sections/CrossSection_AB.png',
        help='Cross-section image to place at the bottom of the combined overview map',
    )
    parser.add_argument(
        '--overview-profile-image',
        default='References/Philmont/Trek_12-15_elevation_profile.png',
        help='Elevation profile image to place in the combined overview map',
    )
    parser.add_argument(
        '--daily-hiking-stats',
        default='DailyGuide/dailiy-hiking-stats.md',
        help='Markdown file with day-by-day hiking summary stats',
    )
    parser.add_argument(
        '--daily-geology',
        default='DailyGuide/daily-geology.md',
        help='Markdown file with day-by-day geology notes',
    )
    parser.add_argument(
        '--daily-riddle',
        default='DailyGuide/daily-riddle.md',
        help='Markdown file with day-by-day riddles',
    )
    parser.add_argument(
        '--daily-look-for-this',
        default='DailyGuide/daily-what-you-will-see.md',
        help='Markdown file with day-by-day "Look For This Today" notes',
    )
    parser.add_argument(
        '--daily-astronomy',
        default='DailyGuide/daily-astronomy.md',
        help='Markdown file with day-by-day astronomy notes',
    )
    parser.add_argument(
        '--daily-scout-skill',
        default='DailyGuide/daily-scout-skill.md',
        help='Markdown table with day-by-day Scout Skill header notes',
    )
    parser.add_argument(
        '--daily-clone-wars-quote',
        default='DailyGuide/daily-clone-wars-quote.md',
        help='Markdown table with day-by-day Clone Wars quotes',
    )
    parser.add_argument(
        '--points-of-interest',
        default='References/GIS/DomesticNames_NM_Text/Names_Subset.shp',
        help='Point of interest shapefile with feature_na labels',
    )
    args = parser.parse_args()

    daily_dir = Path(args.daily_segments)
    if not daily_dir.exists():
        print(f'Daily segments folder not found: {daily_dir}', file=sys.stderr)
        sys.exit(1)

    topo = find_raster(Path(args.topo) if args.topo else Path('References/USGS/USGS_I-425_1-prnt_modified.tif'))
    dem = find_raster(Path(args.dem) if args.dem else Path('References/GIS/Elevation/Elevation_Subset.tif'))
    geology = find_raster(Path(args.geology)) if args.geology else None
    refs = load_reference_layers(Path(args.references), TARGET_CRS)
    daily_hiking_stats = parse_daily_hiking_stats(Path(args.daily_hiking_stats)) if args.daily_hiking_stats else {}
    daily_content = {
        'geology': parse_daily_markdown_sections(Path(args.daily_geology)) if args.daily_geology else {},
        'riddle': parse_daily_markdown_sections(Path(args.daily_riddle)) if args.daily_riddle else {},
        'look_for_this': parse_daily_markdown_sections(Path(args.daily_look_for_this)) if args.daily_look_for_this else {},
        'look_for_this_titles': parse_daily_markdown_headings(Path(args.daily_look_for_this)) if args.daily_look_for_this else {},
        'astronomy': parse_daily_markdown_sections(Path(args.daily_astronomy)) if args.daily_astronomy else {},
        'scout_skill': parse_daily_scout_skills(Path(args.daily_scout_skill)) if args.daily_scout_skill else {},
        'clone_quote': parse_clone_wars_quotes(Path(args.daily_clone_wars_quote)) if args.daily_clone_wars_quote else {},
    }

    points_gdf = read_points_of_interest(Path(args.points_of_interest), TARGET_CRS) if args.points_of_interest else gpd.GeoDataFrame()
    cross_sections_gdf = read_geology_cross_sections(Path(args.geology_cross_sections), TARGET_CRS) if args.geology_cross_sections else gpd.GeoDataFrame()

    outdir = Path(args.outdir)
    outdir.mkdir(parents=True, exist_ok=True)

    pdf_list = []
    shapefiles = sorted(daily_dir.glob('*.shp'))
    if not shapefiles:
        print('No shapefiles found in daily segments folder', file=sys.stderr)
        sys.exit(1)

    if args.combined_overview:
        overview_path = outdir / args.combined_overview_name
        try:
            create_combined_overview_map(
                shapefiles, topo, geology, dem, points_gdf, refs, overview_path,
                buffer_m=args.buffer,
                cross_sections_gdf=cross_sections_gdf,
                cross_section_image=Path(args.cross_section_image) if args.cross_section_image else None,
                overview_profile_image=Path(args.overview_profile_image) if args.overview_profile_image else None,
            )
            print('Wrote combined overview PDF', overview_path)
        except Exception as e:
            warnings.warn(f'Failed to create combined overview map: {e}')

    for shp in shapefiles:
        day_name = shp.stem
        out_pdf = outdir / f'{day_name}.pdf'
        try:
            create_map_for_day(
                shp, topo, geology, dem, points_gdf, refs, out_pdf,
                buffer_m=args.buffer,
                daily_hiking_stats=daily_hiking_stats,
                daily_content=daily_content,
            )
            pdf_list.append(out_pdf)
            print('Wrote', out_pdf)
        except Exception as e:
            warnings.warn(f'Failed to create map for {shp}: {e}')

    if args.combined and pdf_list:
        combined_path = outdir / 'DailyMaps_combined.pdf'
        if PdfReader is None or PdfWriter is None:
            warnings.warn('pypdf is not installed; cannot create combined PDF. Install pypdf to enable this feature.')
        else:
            writer = PdfWriter()
            for p in pdf_list:
                try:
                    reader = PdfReader(str(p))
                    for page in reader.pages:
                        writer.add_page(page)
                except Exception as exc:
                    warnings.warn(f'Could not append {p} to combined PDF: {exc}')
            with open(combined_path, 'wb') as fp:
                writer.write(fp)
            print('Wrote combined PDF', combined_path)


if __name__ == '__main__':
    main()
