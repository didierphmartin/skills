---
name: linkedin-carousel
description: Generate professional LinkedIn carousel posts as PDF documents from text content. Use this skill whenever the user wants to create a LinkedIn carousel, post slides, visual content for LinkedIn, or mentions transforming text into a carousel format. Also trigger when they want to make engaging slide-based content for social media, especially LinkedIn.
dependencies: [fpdf2, pillow]
---

# LinkedIn Carousel Generator

This skill transforms text content into a professional LinkedIn carousel PDF. LinkedIn carousels are multi-slide posts that users can swipe through, and they're highly engaging for professional content.

## What this skill does

Takes text input (either provided directly or from a file) and generates a polished PDF carousel with:
- A title slide
- Content slides with clear, readable text
- Professional design and typography
- Consistent branding and layout
- Optimal sizing for LinkedIn (1080x1080px per slide)

## When to use this skill

Use this skill when the user wants to:
- Create a LinkedIn carousel from text, bullet points, or structured content
- Transform blog posts, articles, or long-form content into swipeable slides
- Design visual content for LinkedIn that's more engaging than plain text
- Make professional slide decks specifically for LinkedIn posts

## How it works

### Input format

The skill accepts text in several formats:

1. **Structured with headers**: Content separated by headers (lines starting with #) becomes individual slides
2. **Numbered/bulleted lists**: Each major point becomes a slide
3. **Plain paragraphs**: The skill intelligently breaks content into digestible slides

The first line or section typically becomes the title slide.

### Slide design principles

LinkedIn carousels work best when they:
- Have 5-10 slides (sweet spot for engagement)
- Use clear, large text (readable on mobile)
- Follow a logical narrative flow
- Have visual hierarchy (title, body, optional footer)
- Use professional colors and spacing

### Output

The skill generates a PDF file where each page is a slide, formatted at 1080x1080px (LinkedIn's optimal square format). The user can then upload this PDF directly to LinkedIn as a carousel post.

## Workflow

1. **Gather the content**: Ask the user for their text content or read it from a provided file

2. **Understand the structure**: Analyze the content to identify:
   - The main title/hook
   - Key points or sections
   - Natural breaks for slides

3. **Plan the carousel**: Decide how to split content across slides. Aim for:
   - Clear title slide with the main message
   - 4-8 content slides with one key idea each
   - Optional closing slide (CTA, summary, or contact info)

4. **Generate the PDF**: Use the bundled `scripts/generate_carousel.py` script:
   ```bash
   python scripts/generate_carousel.py \
     --input <content-file-or-text> \
     --output /outputs/carousel.pdf \
     --theme <professional|modern|minimal> \
     --slides <number-of-slides>
   ```

5. **Review and iterate**: Show the user the PDF. Ask if they want adjustments to:
   - Number of slides
   - Text per slide
   - Visual theme
   - Color scheme

## Theme options

The skill supports multiple visual themes:

- **professional**: LinkedIn blue palette, clean serif fonts, corporate feel
- **modern**: Bold colors, sans-serif fonts, contemporary design
- **minimal**: Black and white, maximum readability, minimalist aesthetic

If the user doesn't specify, use **professional** as the default.

## Best practices

When creating carousels:

- **Hook on slide 1**: The first slide should grab attention with a compelling title or question
- **One idea per slide**: Don't overcrowd slides with text
- **Use white space**: Generous margins and spacing improve readability
- **Readable fonts**: Text should be large enough to read on mobile (minimum 24pt for body text)
- **Logical flow**: Slides should tell a story or build an argument progressively
- **Strong close**: End with a clear takeaway, question, or call-to-action

## Examples

**Example 1: List-based content**

Input:
```
5 Tips for Better LinkedIn Posts

1. Start with a hook
2. Use short paragraphs
3. Add line breaks
4. Include a call-to-action
5. Post consistently
```

Output: 7-slide carousel (title + 5 tips + closing slide)

**Example 2: Article transformation**

Input: A 500-word blog post about remote work productivity

Output: 8-slide carousel breaking down the key insights with:
- Slide 1: "Remote Work Productivity: What Actually Works"
- Slides 2-7: Core strategies (one per slide)
- Slide 8: Summary and CTA

## Error handling

If the content is:
- **Too short** (< 50 words): Suggest the user add more content or create a simple 2-3 slide carousel
- **Too long** (> 1000 words): Recommend condensing or focusing on key points
- **Poorly structured**: Help the user reorganize it before generating

## Technical notes

- PDF pages are 1080x1080px (LinkedIn's square format)
- Colors use hex codes for consistency
- Fonts are embedded for cross-platform compatibility
- The script uses fpdf2 for PDF generation and Pillow for any image handling
- All output files go to `/outputs/` directory

## Script reference

The bundled script `scripts/generate_carousel.py` handles the PDF generation. You can read its docstring for detailed parameter information if needed, but the workflow above covers the standard usage.
