#!/usr/bin/env python3
"""
LinkedIn Carousel PDF Generator

Generates a professional LinkedIn carousel PDF from text content.
Each page is 1080x1080px (LinkedIn's optimal square format).

Usage:
    python generate_carousel.py --input <file_or_text> --output /outputs/carousel.pdf --theme professional --slides auto
"""

import argparse
import json
import os
import re
from pathlib import Path
import fpdf
from fpdf import FPDF
from typing import List, Dict, Tuple

# Locate the Unicode TTFs that ship with fpdf2. The default core fonts
# (Helvetica/Arial/Times) only support Latin-1 and will crash on em-dashes,
# smart quotes, accented characters, etc.
_FPDF_DIR = os.path.dirname(fpdf.__file__)
_DEJAVU_REGULAR = os.path.join(_FPDF_DIR, 'DejaVuSans.ttf')
_DEJAVU_BOLD = os.path.join(_FPDF_DIR, 'DejaVuSans-Bold.ttf')

# Theme definitions
THEMES = {
    "professional": {
        "primary": "#0077B5",  # LinkedIn blue
        "secondary": "#FFFFFF",
        "text": "#000000",
        "accent": "#00A0DC",
        "background": "#F3F6F8"
    },
    "modern": {
        "primary": "#FF6B35",  # Bold orange
        "secondary": "#FFFFFF",
        "text": "#2D3142",
        "accent": "#4ECDC4",
        "background": "#F7FFF7"
    },
    "minimal": {
        "primary": "#000000",
        "secondary": "#FFFFFF",
        "text": "#000000",
        "accent": "#666666",
        "background": "#FFFFFF"
    }
}

class CarouselPDF(FPDF):
    """Custom PDF class for LinkedIn carousel generation."""
    
    def __init__(self, theme: str = "professional"):
        # Initialize with 1080x1080px (converted to mm: 1080px at 72dpi ≈ 381mm)
        super().__init__(format=(381, 381), unit='mm')
        self.theme_colors = THEMES.get(theme, THEMES["professional"])
        self.set_auto_page_break(False)
        # Register a Unicode-capable font so em-dashes, smart quotes, and
        # accented characters render instead of crashing fpdf2's Latin-1
        # core-font path.
        self.add_font('DejaVu', '', _DEJAVU_REGULAR, uni=True)
        self.add_font('DejaVu', 'B', _DEJAVU_BOLD, uni=True)
        
    def hex_to_rgb(self, hex_color: str) -> Tuple[int, int, int]:
        """Convert hex color to RGB tuple."""
        hex_color = hex_color.lstrip('#')
        return tuple(int(hex_color[i:i+2], 16) for i in (0, 2, 4))
    
    def create_title_slide(self, title: str, subtitle: str = ""):
        """Generate the opening title slide."""
        self.add_page()
        
        # Background
        bg_color = self.hex_to_rgb(self.theme_colors["primary"])
        self.set_fill_color(*bg_color)
        self.rect(0, 0, 381, 381, 'F')
        
        # Title text
        text_color = self.hex_to_rgb(self.theme_colors["secondary"])
        self.set_text_color(*text_color)
        self.set_font('DejaVu', 'B', 56)
        
        # Center the title
        self.set_xy(30, 140)
        self.multi_cell(321, 20, title, align='C')
        
        # Subtitle if provided
        if subtitle:
            self.set_font('DejaVu', '', 32)
            self.set_xy(30, 200)
            self.multi_cell(321, 12, subtitle, align='C')
    
    def create_content_slide(self, title: str, content: str, slide_number: int = 0):
        """Generate a content slide."""
        self.add_page()
        
        # Background
        bg_color = self.hex_to_rgb(self.theme_colors["background"])
        self.set_fill_color(*bg_color)
        self.rect(0, 0, 381, 381, 'F')
        
        # Top accent bar
        accent_color = self.hex_to_rgb(self.theme_colors["primary"])
        self.set_fill_color(*accent_color)
        self.rect(0, 0, 381, 20, 'F')
        
        # Title
        text_color = self.hex_to_rgb(self.theme_colors["text"])
        self.set_text_color(*text_color)
        self.set_font('DejaVu', 'B', 40)
        self.set_xy(30, 50)
        self.multi_cell(321, 15, title, align='L')
        
        # Content
        self.set_font('DejaVu', '', 28)
        self.set_xy(30, 120)
        self.multi_cell(321, 12, content, align='L')
        
        # Slide number (small, bottom right)
        if slide_number > 0:
            self.set_font('DejaVu', '', 18)
            self.set_xy(330, 350)
            self.cell(20, 10, str(slide_number), align='R')
    
    def create_closing_slide(self, message: str):
        """Generate a closing/CTA slide."""
        self.add_page()
        
        # Background gradient effect (simplified as solid with accent)
        bg_color = self.hex_to_rgb(self.theme_colors["primary"])
        self.set_fill_color(*bg_color)
        self.rect(0, 0, 381, 381, 'F')
        
        # Message
        text_color = self.hex_to_rgb(self.theme_colors["secondary"])
        self.set_text_color(*text_color)
        self.set_font('DejaVu', 'B', 44)
        self.set_xy(30, 160)
        self.multi_cell(321, 16, message, align='C')

def parse_content(text: str) -> List[Dict[str, str]]:
    """
    Parse text content into structured slides.
    
    Returns a list of dicts with 'type', 'title', and 'content' keys.
    """
    slides = []
    lines = text.strip().split('\n')
    
    # Extract title (first non-empty line or first heading)
    title_line = ""
    content_start = 0
    
    for i, line in enumerate(lines):
        line = line.strip()
        if line:
            if line.startswith('#'):
                title_line = line.lstrip('#').strip()
            else:
                title_line = line
            content_start = i + 1
            break
    
    if title_line:
        slides.append({
            'type': 'title',
            'title': title_line,
            'content': ''
        })
    
    # Parse remaining content
    current_section = None
    current_content = []
    
    for line in lines[content_start:]:
        line = line.strip()
        
        if not line:
            continue
            
        # Check for headers
        if line.startswith('#'):
            # Save previous section
            if current_section:
                slides.append({
                    'type': 'content',
                    'title': current_section,
                    'content': '\n'.join(current_content)
                })
            
            current_section = line.lstrip('#').strip()
            current_content = []
        
        # Check for numbered items
        elif re.match(r'^\d+\.\s+', line):
            # Save previous section
            if current_section and current_content:
                slides.append({
                    'type': 'content',
                    'title': current_section,
                    'content': '\n'.join(current_content)
                })
                current_content = []
            
            # Extract the item
            item_text = re.sub(r'^\d+\.\s+', '', line)
            current_section = item_text.split(':')[0] if ':' in item_text else item_text[:50]
            
            # Rest is content
            if ':' in item_text:
                current_content = [item_text.split(':', 1)[1].strip()]
            else:
                current_content = []
        
        # Check for bullet points
        elif line.startswith(('- ', '• ', '* ')):
            item_text = line.lstrip('- •*').strip()
            if not current_section:
                current_section = item_text[:50]
            else:
                current_content.append('• ' + item_text)
        
        # Regular content
        else:
            current_content.append(line)
    
    # Save last section
    if current_section:
        slides.append({
            'type': 'content',
            'title': current_section,
            'content': '\n'.join(current_content)
        })
    
    # If no structured content found, create simple slides from paragraphs
    if len(slides) <= 1:
        paragraphs = text.split('\n\n')
        slides = [{'type': 'title', 'title': paragraphs[0][:100], 'content': ''}]
        
        for para in paragraphs[1:]:
            if para.strip():
                # Use first sentence as title
                sentences = para.split('.')
                slide_title = sentences[0][:60] + '...' if len(sentences[0]) > 60 else sentences[0]
                slide_content = para.strip()
                
                slides.append({
                    'type': 'content',
                    'title': slide_title,
                    'content': slide_content[:300]  # Limit content length
                })
    
    return slides

def generate_carousel(content: str, output_path: str, theme: str = "professional", max_slides: int = 10) -> str:
    """
    Generate a LinkedIn carousel PDF.
    
    Args:
        content: Text content to transform into carousel
        output_path: Path where PDF should be saved
        theme: Visual theme (professional, modern, minimal)
        max_slides: Maximum number of slides to generate
    
    Returns:
        Path to generated PDF
    """
    # Parse content into slides
    slides = parse_content(content)
    
    # Limit number of slides
    if len(slides) > max_slides:
        slides = slides[:max_slides]
    
    # Create PDF
    pdf = CarouselPDF(theme=theme)
    
    # Generate slides
    slide_num = 0
    for slide in slides:
        if slide['type'] == 'title':
            pdf.create_title_slide(slide['title'], slide['content'])
        elif slide['type'] == 'content':
            slide_num += 1
            pdf.create_content_slide(slide['title'], slide['content'], slide_num)
    
    # Add closing slide if we have multiple slides
    if len(slides) > 2:
        pdf.create_closing_slide("Thanks for reading!\n\nFollow for more insights")
    
    # Save PDF
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    pdf.output(str(output_path))
    
    return str(output_path)

def main():
    parser = argparse.ArgumentParser(description='Generate LinkedIn carousel PDF from text')
    parser.add_argument('--input', '-i', required=True, help='Input text file or direct text')
    parser.add_argument('--output', '-o', default='/outputs/carousel.pdf', help='Output PDF path')
    parser.add_argument('--theme', '-t', default='professional', choices=['professional', 'modern', 'minimal'], help='Visual theme')
    parser.add_argument('--max-slides', type=int, default=10, help='Maximum number of slides')
    
    args = parser.parse_args()
    
    # Read input
    input_path = Path(args.input)
    if input_path.exists():
        content = input_path.read_text(encoding='utf-8')
    else:
        # Treat as direct text
        content = args.input
    
    # Validate content
    if len(content.strip()) < 50:
        print("Error: Content too short. Please provide at least 50 characters of content.")
        return 1
    
    # Generate carousel
    try:
        output_file = generate_carousel(
            content=content,
            output_path=args.output,
            theme=args.theme,
            max_slides=args.max_slides
        )
        print(f"✓ Carousel generated successfully: {output_file}")
        print(f"  Theme: {args.theme}")
        print(f"  Slides: {args.max_slides}")
        return 0
    except Exception as e:
        print(f"Error generating carousel: {e}")
        import traceback
        traceback.print_exc()
        return 1

if __name__ == '__main__':
    exit(main())
