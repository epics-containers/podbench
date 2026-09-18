# Build and publish the docs

From a Podbench checkout:

```bash
uv run --locked tox -e docs
```

This builds Sphinx with warnings treated as errors. Open `build/html/index.html`
to review the output. For a browser preview that reloads when files change:

```bash
uv run --locked tox -e docs-autobuild
```

Follow the local URL printed by the command. Edit Markdown pages in `docs/` and
add them to the appropriate table of contents. Keep tutorials focused on a
complete task and put optional operations in how-to guides.

If the preview reports `address already in use`, another server is using its
port. Stop the previous preview with Ctrl+C in its terminal, or choose another
port:

```bash
uv run --locked tox -e docs-autobuild -- --port 8001
```

This error concerns the preview server; the preceding Sphinx build may have
completed successfully.

The project uses the Diamond Light Source Python Copier template, pinned in
`.copier-answers.yml` with `docs_type: sphinx`. The Sphinx conversion was generated
with:

```bash
uvx copier update --defaults --trust --vcs-ref 5.4.0 --data docs_type=sphinx
```

The template supplies MyST Markdown, the PyData theme, tox build/preview commands
and the versioned GitHub Pages workflow. Podbench keeps concise task guides in
place of the template's sample API and contributor pages.

Pull requests build a downloadable `docs` artifact. Builds on `main` and tags
publish versioned documentation to `gh-pages`. After the first successful publish,
set the repository's **Settings → Pages** source to **Deploy from a branch**,
branch **gh-pages**, directory **/ (root)**. The entry page redirects to `main`.
The periodic workflow checks external links weekly; run it locally with:

```bash
uv run --locked tox -e docs -- -b linkcheck
```
