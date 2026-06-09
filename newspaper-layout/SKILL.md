---
name: newspaper-layout
description: Broadsheet newspaper HTML layout with CSS design system, typography rules, editorial patterns, and multi-column grid.
version: 1.0
tags: [layout, html, newspaper, editorial, css]
visibility: public
category: Layout and design
legacy_skill_id: 1
---

# Newspaper Layout Skill

## Base HTML Shell

Every output must use this structure:

```html
<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>THE RESEARCH HERALD</title>
  <style>
    /* === All CSS goes here === */
  </style>
</head>
<body>
  <!-- === All content goes here === -->
</body>
</html>
```

## CSS Design System

```css
/* === Reset & Base === */
*, *::before, *::after { box-sizing: border-box; margin: 0; padding: 0; }

body {
  font-family: 'Georgia', 'Times New Roman', serif;
  background-color: #f5f0e8;
  color: #1a1a1a;
  font-size: 15px;
  line-height: 1.6;
}

/* === Page Container === */
.newspaper {
  max-width: 1100px;
  margin: 2rem auto;
  padding: 2rem;
  background: #fdfaf4;
  border: 1px solid #ccc;
  box-shadow: 0 4px 24px rgba(0,0,0,0.15);
}

/* === Masthead === */
.masthead {
  text-align: center;
  border-top: 4px double #1a1a1a;
  border-bottom: 4px double #1a1a1a;
  padding: 1rem 0;
  margin-bottom: 0.5rem;
}
.masthead .paper-name {
  font-size: 3.2rem;
  font-weight: 900;
  letter-spacing: 0.08em;
  text-transform: uppercase;
  font-family: 'Georgia', serif;
  line-height: 1.1;
}
.masthead .paper-tagline {
  font-size: 0.85rem;
  font-style: italic;
  color: #555;
  margin-top: 0.25rem;
}
.masthead .edition-bar {
  display: flex;
  justify-content: space-between;
  font-size: 0.78rem;
  text-transform: uppercase;
  letter-spacing: 0.05em;
  border-top: 1px solid #1a1a1a;
  margin-top: 0.5rem;
  padding-top: 0.4rem;
  color: #333;
}

/* === Section Divider === */
.section-divider {
  display: flex;
  align-items: center;
  gap: 0.75rem;
  margin: 1.5rem 0 1rem;
}
.section-divider::before,
.section-divider::after {
  content: '';
  flex: 1;
  height: 2px;
  background: #1a1a1a;
}
.section-divider span {
  font-size: 0.72rem;
  font-weight: 700;
  text-transform: uppercase;
  letter-spacing: 0.15em;
  white-space: nowrap;
  padding: 0 0.5rem;
}

/* === Multi-Column Grid === */
.columns-2 {
  display: grid;
  grid-template-columns: 2fr 1fr;
  gap: 1.5rem;
  margin-bottom: 1.5rem;
}
.columns-3 {
  display: grid;
  grid-template-columns: 1fr 1fr 1fr;
  gap: 1.5rem;
  margin-bottom: 1.5rem;
}
.column {
  border-right: 1px solid #ccc;
  padding-right: 1.5rem;
}
.column:last-child {
  border-right: none;
  padding-right: 0;
}

/* === Headlines === */
.headline-main {
  font-size: 2.2rem;
  font-weight: 900;
  line-height: 1.1;
  text-transform: uppercase;
  letter-spacing: 0.02em;
  margin-bottom: 0.4rem;
  border-bottom: 2px solid #1a1a1a;
  padding-bottom: 0.4rem;
}
.headline-deck {
  font-size: 1.05rem;
  font-weight: 400;
  font-style: italic;
  color: #333;
  margin-bottom: 0.4rem;
  line-height: 1.4;
}
.headline-secondary {
  font-size: 1.35rem;
  font-weight: 700;
  line-height: 1.2;
  margin-bottom: 0.3rem;
  text-transform: uppercase;
}

/* === Byline === */
.byline {
  font-size: 0.78rem;
  text-transform: uppercase;
  letter-spacing: 0.06em;
  color: #555;
  border-bottom: 1px solid #ccc;
  padding-bottom: 0.4rem;
  margin-bottom: 0.75rem;
}
.byline strong { color: #1a1a1a; }

/* === Body Text === */
.article-body p {
  margin-bottom: 0.75rem;
  text-align: justify;
  hyphens: auto;
}
.article-body p:first-child::first-letter {
  font-size: 3.2rem;
  font-weight: 900;
  float: left;
  line-height: 0.8;
  margin: 0.1rem 0.2rem 0 0;
  font-family: 'Georgia', serif;
}

/* === Pull Quote === */
.pull-quote {
  border-top: 3px solid #1a1a1a;
  border-bottom: 3px solid #1a1a1a;
  padding: 0.75rem 1rem;
  margin: 1.25rem 0;
  text-align: center;
}
.pull-quote p {
  font-size: 1.15rem;
  font-style: italic;
  font-weight: 600;
  line-height: 1.4;
  color: #1a1a1a;
}
.pull-quote cite {
  display: block;
  font-size: 0.75rem;
  text-transform: uppercase;
  letter-spacing: 0.06em;
  color: #555;
  margin-top: 0.4rem;
  font-style: normal;
}

/* === Sidebar === */
.sidebar {
  background: #ede8dc;
  border: 1px solid #bbb;
  padding: 1rem;
  font-size: 0.88rem;
}
.sidebar .sidebar-title {
  font-size: 0.72rem;
  font-weight: 700;
  text-transform: uppercase;
  letter-spacing: 0.12em;
  border-bottom: 2px solid #1a1a1a;
  padding-bottom: 0.3rem;
  margin-bottom: 0.6rem;
}
.sidebar ul { list-style: none; padding: 0; }
.sidebar ul li::before { content: "> "; font-weight: bold; }
.sidebar ul li { margin-bottom: 0.3rem; }

/* === Data Desk Box === */
.data-desk {
  border: 2px solid #1a1a1a;
  margin: 1rem 0;
  font-size: 0.85rem;
}
.data-desk .data-desk-title {
  background: #1a1a1a;
  color: #fdfaf4;
  text-align: center;
  font-size: 0.72rem;
  font-weight: 700;
  text-transform: uppercase;
  letter-spacing: 0.15em;
  padding: 0.3rem;
}
.data-desk table { width: 100%; border-collapse: collapse; }
.data-desk td, .data-desk th {
  padding: 0.35rem 0.6rem;
  border-bottom: 1px solid #ccc;
}
.data-desk th {
  font-weight: 700;
  background: #f0ece0;
  text-transform: uppercase;
  font-size: 0.72rem;
  letter-spacing: 0.05em;
}
.data-desk tr:last-child td { border-bottom: none; }

/* === Continued Line === */
.continued {
  font-style: italic;
  font-size: 0.78rem;
  color: #555;
  text-align: right;
  margin-top: 0.5rem;
}

/* === Footer === */
.newspaper-footer {
  border-top: 4px double #1a1a1a;
  margin-top: 2rem;
  padding-top: 0.6rem;
  display: flex;
  justify-content: space-between;
  font-size: 0.72rem;
  color: #555;
  text-transform: uppercase;
  letter-spacing: 0.05em;
}
```

## HTML Layout Patterns

### Masthead
```html
<header class="masthead">
  <div class="paper-name">The Research Herald</div>
  <div class="paper-tagline">"All the Evidence Fit to Print" -- Est. 2024</div>
  <div class="edition-bar">
    <span>Vol. 1 -- Special Edition</span>
    <span>Verified Research Pipeline</span>
    <span>April 6, 2025</span>
  </div>
</header>
```

### Section Divider
```html
<div class="section-divider"><span>Health &amp; Medicine</span></div>
```

### Main Story (2-column: article + sidebar)
```html
<div class="columns-2">
  <div class="column">
    <h1 class="headline-main">Headline Here</h1>
    <h2 class="headline-deck">Subtitle expanding on the headline</h2>
    <div class="byline">
      <strong>By the Research Desk</strong> | Filed: Date | Category
    </div>
    <div class="article-body">
      <p>Lead paragraph (first letter becomes drop cap automatically)...</p>
      <div class="pull-quote">
        <p>"Notable quote from the research."</p>
        <cite>-- Author, Journal, Year</cite>
      </div>
      <p>Continuation paragraph...</p>
    </div>
  </div>
  <div class="column">
    <div class="sidebar">
      <div class="sidebar-title">At a Glance</div>
      <ul>
        <li>Key finding one</li>
        <li>Key finding two</li>
      </ul>
    </div>
  </div>
</div>
```

### Data Desk Box
```html
<div class="data-desk">
  <div class="data-desk-title">Data Desk</div>
  <table>
    <tr><th>Metric</th><th>Value</th></tr>
    <tr><td>Study population</td><td>12,400 patients</td></tr>
    <tr><td>Relative risk reduction</td><td>38% (p &lt; 0.001)</td></tr>
  </table>
</div>
```

### Footer
```html
<footer class="newspaper-footer">
  <span>The Research Herald</span>
  <span>Compiled by the Editorial Pipeline System</span>
  <span>(c) 2025</span>
</footer>
```

## Typography Rules

| Element          | HTML Tag / Class                                     |
|------------------|------------------------------------------------------|
| Newspaper name   | `<div class="paper-name">` -- large, bold, uppercase  |
| Main headline    | `<h1 class="headline-main">` -- uppercase, heavy      |
| Deck / subtitle  | `<h2 class="headline-deck">` -- italic, lighter       |
| Section divider  | `<div class="section-divider">` with centered label  |
| Byline           | `<div class="byline">` -- small caps, muted           |
| Body text        | `<div class="article-body"><p>` -- justified, drop cap|
| Pull quote       | `<div class="pull-quote">` -- bordered top and bottom |
| Sidebar          | `<div class="sidebar">` -- tinted background box      |
| Data table       | `<div class="data-desk">` -- black header, ruled rows |
| Footer           | `<footer class="newspaper-footer">` -- small, spaced  |

## Editorial Writing Style

- **Headlines:** Active voice, present tense, no articles where possible.
- **Deck lines:** Expand on the headline with one clear sentence.
- **Lead paragraph:** Answer who, what, where, when, why in the first 2 sentences.
- **Body paragraphs:** Short (3-5 sentences). Each makes one point.
- **Transitions:** Use editorial connectors -- "Meanwhile," / "Separately," /
  "In a related finding," / "Critics, however, note that..."
- **Quotes:** Always attributed with name, role, and source year inside `<cite>`.

## Multi-Document Handling

When multiple documents or sections are provided:

1. Treat the entire input as a single edition -- each document becomes a story.
2. Most significant finding leads above the fold.
3. Insert `<div class="section-divider">` between each major topic.
4. Cross-reference with inline editorial notes in italics.
5. One masthead, one footer -- the whole output is a single coherent edition.
