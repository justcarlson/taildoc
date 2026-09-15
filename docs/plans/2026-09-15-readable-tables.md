# Readable tables in the shared renderer

## Cause

The reading layout gives each mobile table a fixed 36rem minimum width. A four-column prose table can still allocate less than 70px to a column. The browser tests use short cells and only check page overflow. They accept a locally scrolling table even when its text is hard to read. Browser tests are also skipped in CI.

## Design

Fix the server renderer, not individual reports or agent prompts.

- Render one semantic table with explicit roles, column scopes, and hidden duplicate header labels in its body cells.
- At screen widths up to 640px, display each row as a full-width group of labeled values. Use CSS only. Keep the original header available to assistive technology.
- At larger screen widths, keep the normal table. Give columns a 12rem minimum width and retain the existing keyboard-accessible local scroller when needed.
- Print remains a normal table with no repeated mobile labels or minimum cell width.
- Use the existing reading CSS for all palettes and typography presets. Add no runtime dependency, user setting, model heuristic, JavaScript, or new layout engine.
- Keep custom HTML and old stored versions unchanged. New Markdown publications use the fix automatically. Increment renderer and layout metadata.

## Tests before implementation

Add a prose-heavy four-column fixture that reproduces the reported failure. Verify body cells use the available mobile width, labels remain associated, links and code survive, and there is no page or table scrolling on mobile. Test 320, 375, 390, 640, 641, 768, and 1280px. Keep palette and font coverage. Check print, accessible roles, long strings, empty cells, multiple tables, and deterministic safe markup.

Add a dedicated CI browser job using the same Playwright tests already in the repository. No browser dependency is added to the server installation.

## Delivery

Run the full baseline and regression suite, smoke checks, and browser tests. Commit and merge only the scoped renderer, CSS, test, workflow, and documentation changes. Deploy the pinned revision through the existing rollback-safe installer without changing Serve routes. Verify installed file checksums, active health, publication from the real client, desktop/mobile rendering, and unchanged historical document bytes. Record the source revision in the fleet inventory.

## Rollback and limits

Retain application preimages. Restore the previous application revision if activation or external checks fail. Keep current stored documents; do not roll the data store back over new publications. This fix covers generated Markdown tables. Arbitrary uploaded HTML retains author-owned CSS, and old frozen HTML needs a new publication to receive the new layout.
