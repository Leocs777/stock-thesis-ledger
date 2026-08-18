# Stock Thesis Ledger Component Contracts

Status: local Phase 3 complete; remote Figma synchronization remains blocked by
the Starter MCP tool-call quota.

## Source of truth

| Surface | File | Responsibility |
| --- | --- | --- |
| Web | `web/investor-lab-ui.css` | Semantic tokens, component states, focus and responsive behavior |
| Web demos | `web/investor-lab-ui.js` | Design-system preview state switching only |
| iOS | `ios/InvestorLab/DesignSystem.swift` | Native SwiftUI tokens and reusable views/styles |
| Visual review | `web/design-system.html` | Sanitized component laboratory with no investment data |

## Component API

| Family | Web API | SwiftUI API | Required states |
| --- | --- | --- | --- |
| Button | `.lab-button`, `data-variant`, `data-size` | `LabButtonStyle(variant:compact:)` | default, pressed/focus, disabled, busy at call site |
| Input | `.lab-field`, `.lab-input`, `aria-invalid` | Native `TextField` in native forms | default, focus, error, disabled |
| Badge | `.lab-badge`, `data-tone` | `LabBadge(_:tone:showsIndicator:)` | neutral, positive, warning, negative, blocked |
| Metric | `.lab-metric`, `data-tone` | `LabMetricCard` | neutral, positive, negative |
| Data table | `.lab-table-wrap`, `.lab-table` | Native SwiftUI rows in `LabSection` | populated, empty, loading |
| Navigation | `.lab-nav`, `.lab-nav__item`, `aria-current` | Native `TabView` and segmented `Picker` | default, current/selected |

## Accessibility contract

- Interactive controls have a minimum 44 px/pt target in their regular size.
- Web focus remains visible through `:focus-visible`; demo focus classes are
  preview-only and never replace keyboard focus.
- Status uses text plus color. Badge tones never communicate state by color
  alone.
- Financial values use tabular digits on both platforms.
- iOS retains Dynamic Type, VoiceOver grouping, native tab navigation, and
  native form controls.
- Loading animation respects `prefers-reduced-motion` on Web.

## Change rules

1. Add or change semantic tokens before changing component implementations.
2. Keep the same component intent across Web, iOS, and Figma; platform-native
   interaction is preferred over pixel-identical behavior.
3. New states require an accessible label, a local preview, and Web/iOS mapping
   before they can be added to Figma.
4. Do not create remote Figma components until Phase 2 pages are synchronized
   and validated; this prevents orphaned components under the Starter page cap.
