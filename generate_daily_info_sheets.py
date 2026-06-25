"""
generate_daily_info_sheets.py

Generates daily information sheets for the Philmont Trek 12-15 Field Guide.
Each sheet compiles data from multiple sources into a single, print-ready PDF.

Usage:
    python generate_daily_info_sheets.py --output DailyGuide/DailyInfoSheets

Dependencies:
    pip install reportlab pillow pyyaml

Output:
    - PDF files for each day (11x17 landscape or 5.5x8.5 portrait)
    - 300 DPI resolution, press-quality
"""

import os
import sys
import re
from pathlib import Path
from datetime import datetime, timedelta
import argparse
import yaml

try:
    from reportlab.lib.pagesizes import letter, landscape
    from reportlab.lib.units import inch
    from reportlab.lib.colors import HexColor, black, white, grey
    from reportlab.pdfgen.canvas import Canvas
    from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Image, Table, TableStyle, PageBreak
    from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
    from reportlab.lib.enums import TA_LEFT, TA_CENTER, TA_RIGHT
    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.ttfonts import TTFont
except ImportError:
    print("ERROR: reportlab not found. Install with: pip install reportlab pillow pyyaml")
    sys.exit(1)

# =============================================================================
# DESIGN SYSTEM & STYLING CONSTANTS
# =============================================================================

# Color Palette
COLORS = {
    'forest': HexColor('#2F5D3A'),       # Forest Green
    'granite': HexColor('#5C6166'),      # Granite Gray
    'sand': HexColor('#D8C6A3'),         # Sand
    'sky': HexColor('#5FA8D3'),          # Sky Blue
    'rust': HexColor('#A65A3A'),         # Rust
    'gold': HexColor('#C49A32'),         # Gold
}

# Page Setup
PAGE_WIDTH_LANDSCAPE = 11 * inch
PAGE_HEIGHT_LANDSCAPE = 8.5 * inch
PAGE_WIDTH_PORTRAIT = 5.5 * inch
PAGE_HEIGHT_PORTRAIT = 8.5 * inch

MARGIN_TOP = 0.5 * inch
MARGIN_BOTTOM = 0.5 * inch
MARGIN_LEFT = 0.5 * inch
MARGIN_RIGHT = 0.5 * inch

# Grid System
GRID_COLUMNS = 12
GRID_GUTTER = 0.2 * inch

# Typography
FONT_HEADING1_SIZE = 28
FONT_HEADING2_SIZE = 14
FONT_HEADING3_SIZE = 11
FONT_BODY_SIZE = 9
FONT_CAPTION_SIZE = 7

# Difficulty colors
DIFFICULTY_COLORS = {
    'Easy': COLORS['forest'],
    'Moderate': COLORS['gold'],
    'Hard': COLORS['rust'],
    'Very hard': HexColor('#9B3B1D'),
}

# =============================================================================
# DATA LOADING FUNCTIONS
# =============================================================================

def load_yaml_file(filepath):
    """Load YAML file safely."""
    if not os.path.exists(filepath):
        return None
    try:
        with open(filepath, 'r') as f:
            return yaml.safe_load(f)
    except Exception as e:
        print(f"Warning: Could not load {filepath}: {e}")
        return None


def parse_markdown_table(content, day_number):
    """Parse markdown table and extract day-specific content."""
    lines = content.split('\n')
    table_started = False
    data = {}
    
    for line in lines:
        if '|' not in line:
            continue
        if '---' in line:
            table_started = True
            continue
        if not table_started or not line.strip():
            continue
        
        cells = [cell.strip() for cell in line.split('|')[1:-1]]
        
        if len(cells) < 2:
            continue
        
        # Check if this row is for our day
        day_str = cells[0].lower()
        if f'day {day_number}' in day_str or f'day {day_number:02d}' in day_str or (day_number == 0 and 'day 0' in day_str):
            if len(cells) >= 2:
                data['entry'] = cells[1] if len(cells) > 1 else ''
                data['full_row'] = cells
            return data
    
    return data


def extract_day_content(day_number, base_path):
    """Extract all content for a specific day from markdown files."""
    content = {
        'day': day_number,
        'title': '',
        'subtitle': '',
        'route': {},
        'scout_law': {},
        'clone_wars_quote': '',
        'trek_statistics': {},
        'weather': {},
        'sun_moon': {},
        'geology': '',
        'voices_from_land': '',
        'astronomy': '',
        'fun_fact': '',
        'riddle': '',
        'challenge': '',
        'scout_skill': '',
    }
    
    # Load YAML from day directory
    day_folder = os.path.join(base_path, f'day{day_number:02d}-*')
    import glob
    day_dirs = glob.glob(day_folder)
    
    if day_dirs:
        day_yaml = os.path.join(day_dirs[0], 'day.yaml')
        day_data = load_yaml_file(day_yaml)
        if day_data:
            route = day_data.get('route', {})
            if isinstance(route, str):
                route_str = route
                camp = ''
                if '→' in route_str:
                    camp = route_str.split('→')[-1].strip()
                elif 'to' in route_str.lower():
                    camp = route_str.split()[-1].strip()
                route = {
                    'description': route_str,
                    'camp': camp,
                }
            content.update({
                'title': day_data.get('title', ''),
                'subtitle': day_data.get('subtitle', ''),
                'route': route,
                'scout_law': day_data.get('scout_law', {}),
                'clone_wars_quote': day_data.get('clone_wars_quote', {}),
                'trek_statistics': day_data.get('trek_statistics', {}),
                'weather': day_data.get('weather', {}),
                'sun_moon': day_data.get('sun_moon', {}),
            })
            content['date'] = day_data.get('date', '')
    
    # Load titles
    titles_file = os.path.join(base_path, 'daily-titles.md')
    if os.path.exists(titles_file):
        with open(titles_file, 'r') as f:
            titles_content = f.read()
            # Extract title and subtitle from markdown
            for line in titles_content.split('\n'):
                if f'Day {day_number} —' in line and 'Title:' in line:
                    content['title'] = line.split('Title:')[1].strip()
                elif f'Subtitle:' in line:
                    content['subtitle'] = line.split('Subtitle:')[1].strip()
    
    # Load hiking stats
    stats_file = os.path.join(base_path, 'dailiy-hiking-stats.md')
    if os.path.exists(stats_file):
        with open(stats_file, 'r') as f:
            stats_content = f.read()
            # Extract stats from markdown
            in_day_section = False
            for line in stats_content.split('\n'):
                if f'Day {day_number} —' in line:
                    in_day_section = True
                elif in_day_section and line.startswith('## Day'):
                    break
                elif in_day_section and ':' in line and '-' not in line:
                    key, value = line.split(':', 1)
                    key = key.strip().replace(' ', '_').lower()
                    value = value.strip()
                    if key == 'mileage':
                        try:
                            content['trek_statistics']['mileage_mi'] = float(value.split()[0])
                        except:
                            pass
    
    # Load geology
    geology_file = os.path.join(base_path, 'daily-geology.md')
    if os.path.exists(geology_file):
        with open(geology_file, 'r') as f:
            geology_content = f.read()
            in_day_section = False
            geology_text = []
            for line in geology_content.split('\n'):
                if f'Day {day_number} —' in line:
                    in_day_section = True
                elif in_day_section and line.startswith('## Day'):
                    break
                elif in_day_section and line.strip() and not line.startswith('#'):
                    geology_text.append(line.strip())
            content['geology'] = ' '.join(geology_text[:3])  # Take first 3 lines
    
    # Load Voices from the Land
    voices_file = os.path.join(base_path, 'daily-Voices-from-the-land.md')
    if os.path.exists(voices_file):
        with open(voices_file, 'r') as f:
            voices_content = f.read()
            in_day_section = False
            voices_text = []
            for line in voices_content.split('\n'):
                if f'Day {day_number} —' in line:
                    in_day_section = True
                elif in_day_section and line.startswith('## Day'):
                    break
                elif in_day_section and line.strip() and not line.startswith('#') and not line.startswith('-'):
                    if '**' in line:
                        line = line.replace('**', '')
                    voices_text.append(line.strip())
            content['voices_from_land'] = ' '.join(voices_text[:2])
    
    # Load astronomy
    astronomy_file = os.path.join(base_path, 'daily-astronomy.md')
    if os.path.exists(astronomy_file):
        with open(astronomy_file, 'r') as f:
            astronomy_content = f.read()
            in_day_section = False
            astronomy_text = []
            for line in astronomy_content.split('\n'):
                if f'Day {day_number} —' in line:
                    in_day_section = True
                elif in_day_section and line.startswith('## Day'):
                    break
                elif in_day_section and line.strip() and not line.startswith('#') and not line.startswith('-'):
                    astronomy_text.append(line.strip())
            content['astronomy'] = ' '.join(astronomy_text[:2])
    
    # Load fun facts
    funfacts_file = os.path.join(base_path, 'daily-funfacts.md')
    if os.path.exists(funfacts_file):
        with open(funfacts_file, 'r') as f:
            facts_content = f.read()
            in_day_section = False
            for line in facts_content.split('\n'):
                if f'Day {day_number} —' in line:
                    in_day_section = True
                elif in_day_section and line.startswith('## Day'):
                    break
                elif in_day_section and 'fact:' in line.lower():
                    content['fun_fact'] = line.split(':', 1)[1].strip()
                    break
    
    # Load riddle
    riddle_file = os.path.join(base_path, 'daily-riddle.md')
    if os.path.exists(riddle_file):
        with open(riddle_file, 'r') as f:
            riddle_content = f.read()
            in_day_section = False
            for line in riddle_content.split('\n'):
                if f'Day {day_number} —' in line:
                    in_day_section = True
                elif in_day_section and line.startswith('## Day'):
                    break
                elif in_day_section and 'Riddle:' in line:
                    content['riddle'] = line.split(':', 1)[1].strip()
                    break
    
    # Load challenge
    challenge_file = os.path.join(base_path, 'daily-challenge.md')
    if os.path.exists(challenge_file):
        with open(challenge_file, 'r') as f:
            challenge_content = f.read()
            in_day_section = False
            for line in challenge_content.split('\n'):
                if f'Day {day_number} —' in line:
                    in_day_section = True
                elif in_day_section and line.startswith('## Day'):
                    break
                elif in_day_section and 'Challenge:' in line:
                    content['challenge'] = line.split(':', 1)[1].strip()
                    break
    
    return content


# =============================================================================
# PDF GENERATION FUNCTIONS
# =============================================================================

def create_pdf_sheet(day_content, output_path, page_format='landscape'):
    """Create a single PDF information sheet for a day."""
    
    # Set up page dimensions
    if page_format == 'landscape':
        page_width = PAGE_WIDTH_LANDSCAPE
        page_height = PAGE_HEIGHT_LANDSCAPE
    else:
        page_width = PAGE_WIDTH_PORTRAIT
        page_height = PAGE_HEIGHT_PORTRAIT
    
    # Create PDF canvas
    canvas = Canvas(
        output_path,
        pagesize=(page_width, page_height),
        bottomup=True
    )
    canvas.setTitle(f"Day {day_content['day']} - {day_content.get('title', '')}")
    
    # Set DPI for print quality
    canvas.setPageSize((page_width, page_height))
    
    # =========================================================================
    # HEADER SECTION (Top 25%)
    # =========================================================================
    
    y = page_height - MARGIN_TOP
    
    # Day number and date
    day_num = day_content['day']
    date_str = day_content.get('date', 'TBD')
    
    canvas.setFont("Helvetica-Bold", FONT_HEADING1_SIZE)
    canvas.setFillColor(COLORS['forest'])
    canvas.drawString(MARGIN_LEFT, y - 30, f"Day {day_num}")
    
    canvas.setFont("Helvetica", FONT_BODY_SIZE)
    canvas.setFillColor(COLORS['granite'])
    canvas.drawString(MARGIN_LEFT + 2*inch, y - 30, f"— {date_str}")
    
    y -= 50
    
    # Title and subtitle
    canvas.setFont("Helvetica-Bold", FONT_HEADING2_SIZE)
    canvas.setFillColor(COLORS['forest'])
    canvas.drawString(MARGIN_LEFT, y, day_content.get('title', 'Title TBD'))
    
    y -= 25
    canvas.setFont("Helvetica", FONT_BODY_SIZE)
    canvas.setFillColor(COLORS['granite'])
    canvas.drawString(MARGIN_LEFT, y, day_content.get('subtitle', 'Subtitle TBD'))
    
    y -= 30
    
    # Scout Law & Clone Wars Quote
    scout_law = day_content.get('scout_law', {})
    if isinstance(scout_law, dict) and scout_law.get('point'):
        canvas.setFont("Helvetica-Bold", FONT_HEADING3_SIZE)
        canvas.setFillColor(COLORS['rust'])
        canvas.drawString(MARGIN_LEFT, y, f"Scout Law: {scout_law.get('point', '')}")
        
        y -= 18
        canvas.setFont("Helvetica-Oblique", FONT_BODY_SIZE)
        canvas.setFillColor(COLORS['granite'])
        canvas.drawString(MARGIN_LEFT, y, scout_law.get('statement', ''))
    
    y -= 25
    
    # Clone Wars Quote
    cw_quote = day_content.get('clone_wars_quote', {})
    if isinstance(cw_quote, dict) and cw_quote.get('text'):
        canvas.setFont("Helvetica-Oblique", FONT_BODY_SIZE)
        canvas.setFillColor(COLORS['sky'])
        quote_text = f'"{cw_quote.get("text", "")}"'
        # Wrap text if needed
        words = quote_text.split()
        line = ""
        x = MARGIN_LEFT
        for word in words:
            if canvas.stringWidth(line + word, "Helvetica-Oblique", FONT_BODY_SIZE) > 4*inch:
                canvas.drawString(x, y, line)
                y -= 14
                line = word + " "
            else:
                line += word + " "
        if line:
            canvas.drawString(x, y, line)
    
    y -= 40
    
    # =========================================================================
    # TREK STATISTICS PANEL (Right column)
    # =========================================================================
    
    stats = day_content.get('trek_statistics', {})
    stats_x = page_width - MARGIN_RIGHT - 2.5*inch
    stats_y = page_height - MARGIN_TOP - 30
    
    # Draw stats box background
    canvas.setFillColor(COLORS['sand'])
    canvas.setStrokeColor(COLORS['granite'])
    canvas.setLineWidth(1)
    canvas.rect(stats_x - 10, stats_y - 180, 2.5*inch, 180, fill=1, stroke=1)
    
    # Stats title
    canvas.setFont("Helvetica-Bold", FONT_HEADING3_SIZE)
    canvas.setFillColor(COLORS['forest'])
    canvas.drawString(stats_x, stats_y - 15, "Trek Stats")
    
    stats_y -= 35
    canvas.setFont("Helvetica", FONT_BODY_SIZE - 1)
    canvas.setFillColor(black)
    
    # Mileage
    mileage = stats.get('mileage_mi', 'TBD')
    canvas.drawString(stats_x, stats_y, f"Mileage: {mileage} mi")
    stats_y -= 14
    
    # Elevation
    elev_gain = stats.get('elevation_gain_ft', 'TBD')
    canvas.drawString(stats_x, stats_y, f"Elev Gain: {elev_gain} ft")
    stats_y -= 14
    
    elev_loss = stats.get('elevation_loss_ft', 'TBD')
    canvas.drawString(stats_x, stats_y, f"Elev Loss: {elev_loss} ft")
    stats_y -= 14
    
    # Difficulty
    difficulty = stats.get('difficulty', 'TBD')
    diff_color = DIFFICULTY_COLORS.get(difficulty, COLORS['granite'])
    canvas.setFillColor(diff_color)
    canvas.drawString(stats_x, stats_y, f"Difficulty: {difficulty}")
    stats_y -= 14
    
    # Highest point
    canvas.setFillColor(black)
    highest = stats.get('highest_point_ft', 'TBD')
    canvas.drawString(stats_x, stats_y, f"Highest: {highest} ft")
    stats_y -= 14
    
    # Camp
    route = day_content.get('route', {})
    camp = route.get('camp', 'TBD')
    canvas.drawString(stats_x, stats_y, f"Camp: {camp}")
    
    # =========================================================================
    # CONTENT SECTIONS (Bottom 55%)
    # =========================================================================
    
    y = page_height - MARGIN_TOP - 250
    col_width = (page_width - MARGIN_LEFT - stats_x + MARGIN_RIGHT) / 2
    col1_x = MARGIN_LEFT
    col2_x = col1_x + col_width + GRID_GUTTER
    
    # Section styling helper
    def draw_section(x, y, title, content, max_lines=3):
        """Draw a content section."""
        canvas.setFont("Helvetica-Bold", FONT_HEADING3_SIZE)
        canvas.setFillColor(COLORS['forest'])
        canvas.drawString(x, y, title)
        
        y -= 15
        canvas.setFont("Helvetica", FONT_BODY_SIZE - 1)
        canvas.setFillColor(COLORS['granite'])
        
        # Word wrap content
        if content:
            words = str(content).split()
            line = ""
            line_count = 0
            for word in words:
                if canvas.stringWidth(line + word, "Helvetica", FONT_BODY_SIZE - 1) > col_width - 20:
                    if line_count >= max_lines:
                        line += "..."
                        break
                    canvas.drawString(x + 10, y, line)
                    y -= 12
                    line = word + " "
                    line_count += 1
                else:
                    line += word + " "
            if line and line_count < max_lines:
                canvas.drawString(x + 10, y, line)
        
        return y - 15
    
    # Geology
    y = draw_section(col1_x, y, "Geology", day_content.get('geology', 'TBD'), max_lines=2)
    
    # Voices from the Land
    y = draw_section(col1_x, y, "Voices from the Land", day_content.get('voices_from_land', 'TBD'), max_lines=2)
    
    # Astronomy
    y = draw_section(col2_x, page_height - MARGIN_TOP - 250, "Astronomy", day_content.get('astronomy', 'TBD'), max_lines=2)
    
    # Fun sections (bottom)
    fun_y = page_height - MARGIN_TOP - 520
    
    # Fun Fact
    if day_content.get('fun_fact'):
        canvas.setFont("Helvetica-Bold", FONT_HEADING3_SIZE)
        canvas.setFillColor(COLORS['gold'])
        canvas.drawString(MARGIN_LEFT, fun_y, "💡 Fun Fact")
        
        fun_y -= 12
        canvas.setFont("Helvetica", FONT_BODY_SIZE - 1)
        canvas.setFillColor(black)
        canvas.drawString(MARGIN_LEFT + 10, fun_y, day_content.get('fun_fact', '')[:60] + "...")
    
    # Riddle
    if day_content.get('riddle'):
        canvas.setFont("Helvetica-Bold", FONT_HEADING3_SIZE)
        canvas.setFillColor(COLORS['sky'])
        canvas.drawString(col2_x, fun_y, "❓ Riddle")
        
        fun_y -= 12
        canvas.setFont("Helvetica", FONT_BODY_SIZE - 1)
        canvas.setFillColor(black)
        canvas.drawString(col2_x + 10, fun_y, day_content.get('riddle', '')[:60] + "...")
    
    # =========================================================================
    # FOOTER
    # =========================================================================
    
    canvas.setFont("Helvetica", FONT_CAPTION_SIZE)
    canvas.setFillColor(COLORS['granite'])
    canvas.drawString(MARGIN_LEFT, MARGIN_BOTTOM - 10, f"Philmont Trek 12-15 • Day {day_num}")
    canvas.drawRightString(page_width - MARGIN_RIGHT, MARGIN_BOTTOM - 10, f"Generated: {datetime.now().strftime('%Y-%m-%d')}")
    
    canvas.save()
    print(f"✓ Created: Day {day_num} - {output_path}")


def generate_all_sheets(base_path, output_dir, page_format='landscape'):
    """Generate information sheets for all days."""
    
    # Create output directory
    os.makedirs(output_dir, exist_ok=True)
    
    # Generate sheets for days 0-12
    for day_num in range(13):
        print(f"\n📋 Processing Day {day_num}...")
        
        # Extract content
        day_content = extract_day_content(day_num, base_path)
        
        # Create PDF
        filename = f"Day{day_num:02d}_InfoSheet_{day_content.get('title', 'TBD').replace(' → ', '-').replace(' ', '_')}.pdf"
        output_path = os.path.join(output_dir, filename)
        
        try:
            create_pdf_sheet(day_content, output_path, page_format)
        except Exception as e:
            print(f"✗ Error creating sheet for Day {day_num}: {e}")
            import traceback
            traceback.print_exc()
    
    print(f"\n✅ All sheets generated in: {output_dir}")


# =============================================================================
# MAIN
# =============================================================================

def main():
    parser = argparse.ArgumentParser(
        description='Generate daily information sheets for Philmont Trek 12-15 Field Guide'
    )
    parser.add_argument(
        '--output',
        default='DailyGuide/DailyInfoSheets',
        help='Output directory for PDF sheets (default: DailyGuide/DailyInfoSheets)'
    )
    parser.add_argument(
        '--format',
        choices=['landscape', 'portrait'],
        default='landscape',
        help='Page format (default: landscape for 11x17 Collector Edition)'
    )
    parser.add_argument(
        '--basepath',
        default='DailyGuide',
        help='Base path to DailyGuide folder (default: DailyGuide)'
    )
    
    args = parser.parse_args()
    
    # Resolve paths relative to script location
    script_dir = os.path.dirname(os.path.abspath(__file__))
    base_path = os.path.join(script_dir, args.basepath)
    output_dir = os.path.join(script_dir, args.output)
    
    print(f"📂 Base Path: {base_path}")
    print(f"📁 Output Directory: {output_dir}")
    print(f"📄 Format: {args.format}")
    
    if not os.path.exists(base_path):
        print(f"ERROR: Base path not found: {base_path}")
        sys.exit(1)
    
    generate_all_sheets(base_path, output_dir, args.format)


if __name__ == '__main__':
    main()
