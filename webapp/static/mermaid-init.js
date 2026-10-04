// Renders the <pre class="mermaid"> diagrams on the /support/ pages.
// Kept as a separate file (not an inline <script>) because the site's CSP
// is script-src 'self' - inline scripts and CDN scripts are both blocked.
mermaid.initialize({ startOnLoad: true, theme: "dark" });
