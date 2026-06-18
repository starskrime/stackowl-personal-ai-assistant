import { describe, it, expect } from "vitest";
import { parseGoogleHtml } from "../../src/browser/google-parser.js";

const JSON_LD_HTML = `<html><head>
<script type="application/ld+json">
{
  "@context": "https://schema.org",
  "@type": "SearchResultsPage",
  "mainEntity": {
    "@type": "ItemList",
    "itemListElement": [
      {
        "@type": "ListItem",
        "position": 1,
        "name": "Best Coffee Shops NYC",
        "url": "https://example.com/coffee",
        "description": "Top 10 coffee shops in New York City."
      }
    ]
  }
}
</script></head><body></body></html>`;

const JSON_LD_ARRAY_TYPE_HTML = `<html><head>
<script type="application/ld+json">
{
  "@context": "https://schema.org",
  "@type": ["SearchResultsPage", "ItemList"],
  "itemListElement": [
    {
      "@type": "ListItem",
      "position": 1,
      "name": "Array Type Result",
      "url": "https://example.com/array-type"
    }
  ]
}
</script></head><body></body></html>`;

const DIV_G_HTML = `<html><body>
<div class="g">
  <h3><a href="https://example.com/divg">Div G Result</a></h3>
  <span>Snippet text for div.g result</span>
</div>
</body></html>`;

const HVEID_HTML = `<html><body>
<div data-hveid="ABC123">
  <h3><a href="https://example.com/hveid">Hveid Result</a></h3>
</div>
</body></html>`;

const CAPTCHA_HTML = `<html><body>
<h1>Before you continue</h1>
<p>This page checks to see if it's really you sending the requests.</p>
<form id="captcha-form"></form>
</body></html>`;

const ENCODED_URL_HTML = `<html><body>
<div class="g">
  <h3><a href="https%3A%2F%2Fexample.com%2Fencoded">Encoded URL Result</a></h3>
</div>
</body></html>`;

const AMP_ENTITY_HTML = `<html><body>
<div class="g">
  <h3><a href="https://example.com/page?a=1&amp;b=2">Amp Entity Result</a></h3>
</div>
</body></html>`;

const GOOGLE_REDIRECT_HTML = `<html><body>
<div class="g">
  <h3><a href="https://www.google.com/url?q=https%3A%2F%2Fexample.com%2Ftarget">Redirect Result</a></h3>
</div>
</body></html>`;

describe("parseGoogleHtml", () => {
  it("parses JSON-LD with scalar @type SearchResultsPage", () => {
    const results = parseGoogleHtml(JSON_LD_HTML, "coffee shops nyc");
    expect(results).toHaveLength(1);
    expect(results[0]).toMatchObject({
      title: "Best Coffee Shops NYC",
      url: "https://example.com/coffee",
      snippet: "Top 10 coffee shops in New York City.",
    });
  });

  it("parses JSON-LD with array @type containing SearchResultsPage", () => {
    const results = parseGoogleHtml(JSON_LD_ARRAY_TYPE_HTML, "test");
    expect(results).toHaveLength(1);
    expect(results[0].url).toBe("https://example.com/array-type");
  });

  it("falls back to div.g h3 a when no JSON-LD", () => {
    const results = parseGoogleHtml(DIV_G_HTML, "test");
    expect(results).toHaveLength(1);
    expect(results[0].title).toBe("Div G Result");
    expect(results[0].url).toBe("https://example.com/divg");
  });

  it("falls back to [data-hveid] h3 a as third option", () => {
    const results = parseGoogleHtml(HVEID_HTML, "test");
    expect(results).toHaveLength(1);
    expect(results[0].title).toBe("Hveid Result");
    expect(results[0].url).toBe("https://example.com/hveid");
  });

  it("returns [] on CAPTCHA HTML without throwing", () => {
    expect(() => parseGoogleHtml(CAPTCHA_HTML, "test")).not.toThrow();
    expect(parseGoogleHtml(CAPTCHA_HTML, "test")).toEqual([]);
  });

  it("decodes percent-encoded URLs", () => {
    const results = parseGoogleHtml(ENCODED_URL_HTML, "test");
    expect(results[0]?.url).toBe("https://example.com/encoded");
  });

  it("decodes &amp; HTML entities in URLs", () => {
    const results = parseGoogleHtml(AMP_ENTITY_HTML, "test");
    expect(results).toHaveLength(1);
    expect(results[0].url).toBe("https://example.com/page?a=1&b=2");
  });

  it("returns google.com/url?q= redirect as-is (raw scrape behavior)", () => {
    // Document that /url?q= redirects are returned as-is — caller can unwrap if needed
    const results = parseGoogleHtml(GOOGLE_REDIRECT_HTML, "test");
    // The redirect URL passes http check but contains google.com/url —
    // with the hostname+path filter, /url is NOT in the blocklist, so it passes through
    expect(results).toHaveLength(1);
    expect(results[0].url).toContain("google.com/url?q=");
  });
});
