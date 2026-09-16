// Minimal Trusted Types ambient typings (AD-36, NFR22).
//
// This project's pinned TypeScript (6.0.3) DOM lib does not yet ship the
// Trusted Types API surface, so it is declared here rather than pulling in
// a whole extra @types package for three interfaces and one global. Kept
// deliberately narrow: only the members this kit's one policy actually
// uses (App.svelte's `createHTML`).
interface TrustedHTML {
  readonly __trustedHTMLBrand: unique symbol;
}

interface TrustedTypePolicyOptions {
  createHTML?: (input: string) => string;
  createScript?: (input: string) => string;
  createScriptURL?: (input: string) => string;
}

interface TrustedTypePolicy {
  readonly name: string;
  createHTML(input: string): TrustedHTML;
}

interface TrustedTypePolicyFactory {
  createPolicy(name: string, options: TrustedTypePolicyOptions): TrustedTypePolicy;
}

interface Window {
  readonly trustedTypes?: TrustedTypePolicyFactory;
}
