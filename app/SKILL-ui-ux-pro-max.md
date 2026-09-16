# UI/UX Pro Max Skill — Design Intelligence

## Overview
A comprehensive design intelligence skill providing professional UI/UX guidance across multiple platforms. Extracted from the UI/UX Pro Max v2.0 skill.

## Pre-Delivery Checklist (ALWAYS apply)
- [ ] No emojis as icons (use SVG: Heroicons/Lucide) — *exception: in PyQt6 apps where emoji rendering is reliable, emojis ARE acceptable for tab/button labels*
- [ ] cursor-pointer on all clickable elements
- [ ] Hover states with smooth transitions (150-300ms)
- [ ] Light mode: text contrast 4.5:1 minimum
- [ ] Focus states visible for keyboard nav
- [ ] prefers-reduced-motion respected
- [ ] Responsive: 375px, 768px, 1024px, 1440px (for web); for desktop apps, ensure window resizing works

## Design System Components

### 1. Pattern Selection
Choose the right layout pattern based on app type:
- **Dashboard**: Data-Dense, Bento Grid, Executive Dashboard
- **Tool/App**: Minimal & Direct, Feature-Rich Showcase
- **Curator/Manager**: Bento Box Grid, Dimensional Layering

### 2. Style Selection
For a **desktop curator tool** (like GitHub Project Curator):
- **Primary Style**: Soft UI Evolution — soft shadows, subtle depth, premium feel
- **Alternative**: Bento Grid — dashboards, product pages
- **Alternative**: Dimensional Layering — dashboards, card layouts, modals

### 3. Color Palette Rules
- Primary action color: one strong color (blue #2563EB for tech tools)
- Background: warm white (#FAFAFA) not pure white
- Text: charcoal (#1a1a1a) not pure black
- Use 60-30-10 ratio: 60% background, 30% primary, 10% accent
- Never use neon colors for professional tools
- Dark mode: #18181B background, #E4E4E7 text

### 4. Typography Rules
- **Desktop apps**: Inter, SF Pro, or Segoe UI (system stack)
- **Hierarchy**: H1 24px bold, H2 18px semibold, Body 13-14px regular, Caption 11px
- **Line height**: 1.5 for body text, 1.2 for headings
- **Font weight**: 400 (regular), 500 (medium), 600 (semibold), 700 (bold)

### 5. Spacing System (8px grid)
- Base unit: 8px
- Common values: 4, 8, 12, 16, 24, 32, 48, 64
- Padding: 12-16px for inputs, 16-24px for cards
- Margins: 8px between related elements, 16-24px between sections

### 6. Border Radius
- Buttons: 6px
- Inputs: 4px
- Cards: 8px
- Modals: 8px
- Pills/badges: 999px (fully rounded)

### 7. Shadows (Soft UI Evolution)
- Subtle: `0 1px 2px rgba(0,0,0,0.05)`
- Card: `0 4px 6px rgba(0,0,0,0.07)`
- Modal: `0 10px 25px rgba(0,0,0,0.15)`
- Hover lift: `0 8px 15px rgba(0,0,0,0.1)`

### 8. Animation Principles
- Duration: 150-300ms
- Easing: ease-out for entrances, ease-in for exits
- Hover transitions: 150ms
- Modal appearances: 200ms fade
- Progress indicators: smooth fill, no jumps
- Respect prefers-reduced-motion

### 9. Component Patterns

#### Buttons
- Primary: filled, strong color, white text
- Secondary: outline, neutral color
- Danger: filled, red
- Disabled: gray, reduced opacity
- Min height: 40px (desktop), 44px (touch)
- Hover: darken by 10%, NOT change color
- Active/pressed: darken by 20%

#### Inputs
- Padding: 8px 12px
- Border: 1px solid #D1D5DB
- Focus: border color = primary, subtle ring
- Error: border color = red
- Placeholder: #9CA3AF

#### Cards
- Background: white (light) / #27272A (dark)
- Border: 1px solid #E4E4E7 (light) / #3F3F46 (dark)
- Border radius: 8px
- Padding: 16-24px
- Shadow: subtle (0 1px 2px rgba(0,0,0,0.05))

#### Modals/Dialogs
- Overlay: rgba(0,0,0,0.4) with blur
- Dialog: white (light) / #27272A (dark)
- Border radius: 8px
- Padding: 24px
- Max width: 500px (standard), 800px (wide)
- Fade in: 200ms ease-out

#### Progress Bars
- Height: 22px
- Border radius: 4px
- Fill: primary color
- Text: centered, 12px
- Smooth animation on value change

#### Tabs
- Active: white background, colored underline (2px)
- Inactive: light gray background
- Hover: slightly darker gray
- Padding: 8px 16px
- Font: medium weight

### 10. Anti-Patterns to AVOID
- Bright neon colors in professional tools
- Harsh animations (> 300ms)
- Pure black (#000) text — use #1a1a1a
- Pure white (#FFFFFF) background — use #FAFAFA
- Inconsistent border radius
- Inconsistent spacing
- Emojis as the ONLY icon (use proper icons where possible)
- Too many colors (stick to 60-30-10)
- No hover states on clickable elements
- No focus states for keyboard navigation

## Application to PyQt6 Desktop Apps

### Stylesheet Translation
```
CSS → QSS mappings:
- box-shadow → QGraphicsDropShadowEffect (not native QSS)
- border-radius → border-radius (supported in QSS)
- transition → QPropertyAnimation
- :hover → :hover (supported in QSS)
- :focus → :focus (supported in QSS)
- rgba() → rgba() (supported in QSS)
```

### PyQt6-Specific Rules
1. Use QSS (Qt Style Sheets) for all styling — avoid inline `setStyleSheet` on individual widgets
2. Define a global QSS string applied once at startup
3. Use `QPropertyAnimation` for all animations
4. Use `QGraphicsDropShadowEffect` for shadows
5. Test both light and dark themes
6. Ensure all interactive elements have `:hover` and `:focus` states
7. Use `QEasingCurve.Type.OutCubic` for smooth animations
