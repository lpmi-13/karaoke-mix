# Accessibility audit

Audited on 17 September 2026 against WCAG 2.2 Level A and AA.

## Scope

The audit covers the loading and error states, initial song finder, search suggestions, expanded catalog results, genre selection and browsing, empty and populated tempo results, expanded match results, count-in control, saved-match controls, and the modal “My set” drawer. Checks run in desktop Chromium and an emulated mobile Chromium viewport.

## Checks

- Automated axe-core rules tagged for WCAG 2.0, 2.1, and 2.2 A/AA.
- Full keyboard operation, including skip navigation, tab order, tab/listbox/combobox arrow keys, focus trapping, Escape handling, and focus restoration.
- Responsive reflow at 320 CSS pixels without horizontal page scrolling.
- WCAG text-spacing overrides without lost or clipped controls or content.
- Reduced-motion behavior and persistent visible focus indicators.
- Text contrast, control-boundary contrast, headings, landmarks, names, roles, states, and live status changes.

## Remediation completed

- Rebuilt the song and genre pickers as valid ARIA comboboxes with active descendants and keyboard selection.
- Added correct tab and listbox keyboard patterns.
- Added skip navigation and deterministic focus movement when content opens, closes, is selected, or is removed.
- Made the drawer modal to assistive technology, trapped focus, and restored focus on close.
- Added unique accessible names and pressed states to match controls, band controls, and the count-in.
- Added a programmatic match score, live result counts, labeled result regions, and dialog description.
- Corrected low-contrast text and strengthened interactive control boundaries.
- Added a consistent high-visibility focus indicator and preserved reduced-motion support.
- Prevented hidden/collapsed controls and modal background content from entering the accessibility tree or tab order.

## Regression checks

Run:

```sh
npm run build
npm run test:a11y
npm run test:e2e
```

At the time of this audit, the production build succeeds and all 32 desktop/mobile tests pass, including 14 dedicated accessibility checks. No axe-core WCAG A/AA violations remain in the audited states.

Automated tools cannot by themselves certify conformance. The automated suite is paired with the keyboard, focus, reflow, text-spacing, motion, and visual checks above; formal certification across a named screen-reader/browser matrix would require testing on those external platforms.
